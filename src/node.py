"""
API endpoints for RRMQ server
"""
from queue import Queue
import sys
import grpc # type: ignore
import threading
from collections import defaultdict
from concurrent import futures
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from flask import Flask # type: ignore

import node_connection_pb2 # type: ignore
import node_connection_pb2_grpc # type: ignore

import flask_server
from helpers import CandidateValueComparison, Entry, ErrorCode, Logs, NodeOperationResult, NodeState, Operation, PeersDict, TopicsDict, parseAndValidateServerLaunchArguments, NodeId, PeerList
from timer import ResettableTimer


import datetime

#---------------------------------------------------------------------------------------
# RAFT
class Node(node_connection_pb2_grpc.NodeConnectionServicer):
    def __init__(self, id: NodeId, peers: PeerList) -> None:
        """ Meta Data for server """
        self.ip: str = id[0]
        self.port: int = id[1]

        """ Raft State """
        # an election timer will run out and a leader will be established
        self.state: NodeState = NodeState.FOLLOWER
        self.logs: Logs = []
        self.term: int = 1
        self.commitIndex: int = -1 # highest index in self.logs which has been committed. An operation is considered committed once it has been applied to self.topics

        # NOTE: I think not needed to be in the init, so leaving at the bottom for later reference
        # TODO: if we just care about the presence of a term in this dictionary, we can make it a set, instead of a dict
        self.terms_voted: Set[int] = set()

        self.topics: TopicsDict = {} # stores queues of messages by topic

        """ RPC Server """
        # dictionary mapping node id to the client stub. This will allow this server to communicate with all of its peers over rpc
        self.peer_stubs: PeersDict = {}
        self.init_peer_connections(peers)
        
        # If the swarm has no other nodes, become a leader immediately
        if not len(self.peer_stubs):
            self.update_state(NodeState.LEADER)

        # start rpc server on a secondary thread
        newThread = threading.Thread(target=self.serve)
        newThread.start()

        """ Timers: Election and HearBeat"""
        # NOTE: for testing, pass it 2 and 3 seconds for interval
        # NOTE: election timer doesn't start running upon instantiation - could change that though
        self.election_timer: ResettableTimer = self.get_election_timer_and_bind_to_start_election_fn(200., 300.)
        self.election_timer.run()

        self.heartbeat_timer: ResettableTimer = ResettableTimer(self.send_heartbeats, 200, 200)

    """ 
    Start: methods to access and alter the Node's state 
    """
    def get_election_timer_and_bind_to_start_election_fn(self, start_interval_seconds: float, end_interval_seconds: float) -> ResettableTimer:
        ms_in_second: int = 1

        return ResettableTimer(self.start_election, start_interval_seconds * ms_in_second, end_interval_seconds * ms_in_second)

    def update_state(self, new_state: NodeState):
        self.state = new_state

    # return: term of the last log entry
    def get_last_log_term(self) -> int:
        if self.logs == []:
            return -1
        return self.logs[-1].term

    # return: index of the last log entry
    def get_last_log_index(self) -> int:
        return len(self.logs) - 1

    # returns the operation from self.Logs to commit. This is done by returning self.Logs[commitIndex + 1]
    def get_entry_to_commit(self) -> Optional[Entry]:
        print("getting an entry to commit")
        try:
            return self.logs[self.commitIndex+1]
        except Exception:
            print(f"GET ENTRY - No entry to commit")
            return None

    # perform multiple commits in a row if necessry to advance our state machine to that of the Leader
    # specifically, perform commits while Node.commitIndex <= stopping_index
    def execute_multiple_commits(self, stopping_index: int):
        # progress toward the base case because commit_entry increments commitIndex
        
        while(self.commitIndex < len(self.logs) and self.commitIndex < stopping_index):
            self.commit_entry()

    # execute the operation on the state (i.e. apply the operation to self.topics, altering the message queue)
    def commit_entry(self):
        """Commit entry is only called by FOLLOWER nodes to commit an entry from their logs

        The leader only implicitly commits from the logs, in fact, they just add the entry to their logs, perform the operation of the entry and increment their commit index
        
        """
        # Step 1: get Entry to commit
        entry: Optional[Entry] = self.get_entry_to_commit()

        if entry is not None:

            self.commitIndex += 1 # only increment if we have actually found an entry to execute. 
            
            print(f'commit_entry() executed. Term: {entry.term}, operation: {entry.operation.operation}, message: {entry.operation.message}, topic: {entry.operation.topic}')
            
            if  entry.operation.operation == Operation.GET_TOPIC.value: 
                # the followers should not return a value for Get operations. Just let their commit index increment
                print(self.topics.keys())     

            elif entry.operation.operation == Operation.PUT_TOPIC.value:
                
                # just create a new queue for the topic
                if entry.operation.topic in self.topics:
                    print("MQ Error - Topic already exists in my message queue")
                else:
                    self.topics[entry.operation.topic] = Queue()
                    print(f"MQ - INFO : My topics are now {self.topics.keys()}")

            elif entry.operation.operation == Operation.GET_MESSAGE.value:
                # the followers should not return a value for Get operations. Just let their commit index increment
                self.topics[entry.operation.topic].get()
            elif entry.operation.operation == Operation.PUT_MESSAGE.value:
                self.topics[entry.operation.topic].put(entry.operation.message)
            else:
                print(f'COMMITING - ERROR: commit_entry() received an unrecognized operation: {entry.operation}')
        else:
            print("No entry to commit yet")

    def send_to_followers(self, entry):

        prev_index: int = self.get_last_log_index()
        prev_log_term: int = self.get_last_log_term()

        num_successes = self.fan_out_append_entry_rpcs(entry, prev_index, prev_log_term)
      
        should_commit: bool = num_successes >= self.get_minimum_vote_for_majority()
    
        return should_commit, num_successes

    # returns True if this node is the leader, false otherwise
    def is_leader(self) -> bool:
        return self.state == NodeState.LEADER

    # returns the minimum number of votes from the cluster for a majority
    def get_minimum_vote_for_majority(self):
        if len(self.peer_stubs)==0:
            return 0
        if len(self.peer_stubs)==1:
            return 1
        return (len(self.peer_stubs)+1)//2+1

    """ 
    End: methods to access and alter the Node's state 
    """

    """
    Start: Implementation of RPC Service interface
    """
    # start the grpc server and listen for incoming remote procedure calls
    def serve(self) -> None:
        # TODO: think carefully about a more holistic threading strategy for this project
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        node_connection_pb2_grpc.add_NodeConnectionServicer_to_server(self, server)
        server.add_insecure_port(f'[::]:{self.port}')
        server.start()

        print(f'RPC Server started, listening on {self.ip}:{self.port}')
        server.wait_for_termination()

    # initializes and creates all the client stubs, connecting this server to its peers
    def init_peer_connections(self, peers: PeerList) -> None:
        self.peer_stubs = {id : node_connection_pb2_grpc.NodeConnectionStub(grpc.insecure_channel(f'{id[0]}:{id[1]}')) for id in peers}

    def requestVote(self, request, context) -> node_connection_pb2.VoteResponse:
        # print(f'RAFT - INFO: requestVote invoked by {request.candidate_id}. Term: {request.term} | Last Log Index: {request.last_log_index} | Last Log Term: {request.last_log_term}')
        
        candidate_term_comparison: CandidateValueComparison = self.get_candidate_comparison(self.get_last_log_term, request.last_log_term)
        candidate_log_comparison: CandidateValueComparison = self.get_candidate_comparison(self.get_last_log_index, request.last_log_index)

        # NOTE: candidate and myself can have the same term. We both start with the same term and
        # We can run an election at roughly same time, so by the time I receive the requestVote
        # I will have also incremented my term. 
        candidate_term_at_least_equal: bool = candidate_term_comparison == CandidateValueComparison.CANDIDATE_VALUE_GREATER or candidate_term_comparison == CandidateValueComparison.CANDIDATE_VALUE_SAME

        # NOTE: If logs have last entries with different terms, then the log with the later term is more # up to date. If the logs end with the same term, then the one which is longer is more up to date
        candidate_most_up_to_date: bool = candidate_term_comparison == CandidateValueComparison.CANDIDATE_VALUE_GREATER or candidate_log_comparison == CandidateValueComparison.CANDIDATE_VALUE_GREATER or candidate_log_comparison == CandidateValueComparison.CANDIDATE_VALUE_SAME
        
        allowed_to_accept_vote = candidate_term_at_least_equal and candidate_most_up_to_date
        
        # candidate is most up to date and we haven't voted in this term yet. Grant vote
        if allowed_to_accept_vote and request.term not in self.terms_voted:
            self.terms_voted.add(request.term)
            
            return node_connection_pb2.VoteResponse(term=self.term, vote_granted=True)

        return node_connection_pb2.VoteResponse(term=self.term, vote_granted=False)

    def appendEntries(self, request, context) -> node_connection_pb2.AppendEntriesResponse:
        """AppendEntriesRPC
        
        Receive the request for an AppendEntry, follow the raft implementation rules
        to decide if the AppendEntry is accepted or rejected.

        Nodes reset their timers after receiving an appendEntry request

        Parameters
        ----------

        Returns
        -------
        AppendEntriesResponse
            success: bool
            - True if append entry accepted, False otherwise
            term: int
            - The current term of this node
        """

        self.election_timer.reset()

        entries: List[node_connection_pb2.OperationDetails] = request.entries

        # Receive a heartbeat - do not append  to the log
        if len(entries) == 0:
            self.update_state(NodeState.FOLLOWER)
            # print("RAFT - INFO : Heartbeat received from leader")

            # execute remaining entries
            self.execute_multiple_commits(request.leader_commit)

            return node_connection_pb2.AppendEntriesResponse(term=self.term, success=True)

        entry_details: node_connection_pb2.OperationDetails = entries[0]
        # NOTE: Consider making these sparser, once we are working consitently
        # print(f'RAFT - INFO : appendEntry invoked by {request.leader_id} - Requester details: | Term: {request.term} | Prev Log Index: {request.prev_log_index} | Prev Log Term: {request.prev_log_term} | Operation: {entry_details.operation}, Topic: {entry_details.topic}, Message: {entry_details.message}')
        # print(f'RAFT - INFO : appendEntry invoked by {request.leader_id} - Our details: term: {self.term}, last log index: {self.get_last_log_index()}, previous log term: {self.get_last_log_term()}')

        # REJECT REQUEST - PAPER CHECK PASSED - AppendEntry bullet point 1.
        if request.term < self.term:
            print("RAFT - INFO: Log Inconsistency -> Rejected AppendEntry. I have a more recent term")
            return node_connection_pb2.AppendEntriesResponse(term=self.term, success=False)

        # check to make sure that my version of Node.logs is identical to the leader's version of Node.logs
        # we do this by checking the index in logs immediately prior to the new entries we're adding
        # PAPER CHECK PASSED - AppendEntry bullet point 2.
        bothLogsWereEmptyCheckFail: bool = (request.prev_log_index == -1 or self.get_last_log_index() == -1) and (request.prev_log_index != self.get_last_log_index()) # case where Node.logs is empty on requester
        # we should exit because one of our logs is empty and the other node's is not
        if bothLogsWereEmptyCheckFail:
            print("RAFT - INFO : Log Inconsistency -> Rejected Append Entry. One of the logs (between us and leader) is empty, but the other one is not empty")
            return node_connection_pb2.AppendEntriesResponse(term=self.term, success=False)

        new_entry: Entry = Entry(request.term, entry_details)

        # if it is -1, no further checks required
        if request.prev_log_index != -1:
            passesInboundsCheck: bool = request.prev_log_index <= self.get_last_log_index()
            passesEqualityCheck: bool = request.prev_log_term == self.logs[request.prev_log_index].term
            print(f'passesInboundsCheck: {passesInboundsCheck} | passesEqualityCheck: {passesEqualityCheck}')

            if not passesInboundsCheck:
                # print("RAFT - INFO : Log Inconsistency -> Rejected Append Entry. Node log does not contain an entry at prev_log_index whose term matches prev_log_term")
                print("RAFT - INFO : Log Inconsistency -> Rejected Append Entry. Leader's previous log index is greater than ours.")
                return node_connection_pb2.AppendEntriesResponse(term=self.term, success=False)

            if not passesEqualityCheck:
                print("RAFT - INFO : Log Inconsistency -> Rejected Append Entry. Node log does not contain an entry at prev_log_index whose term matches prev_log_term")
                return node_connection_pb2.AppendEntriesResponse(term=self.term, success=False)

            # check next entry in Node.logs and delete it and all following entries if it is in conflict with the new entry
            # specifically, if there is an entry at Node.logs[prev_log_index + 1] with a term not equal to entries[0].term, deleted this entry and all following
            # There is an entry in my logs with the same index as prev_log_index+1 but they have a different term - there is a conflict
            entry_index: int = request.prev_log_index + 1
            
            try:
                if self.logs[entry_index] and self.logs[entry_index].term != new_entry.term:
                    # delete this and all that follow, then append new
                    self.logs = self.logs[:entry_index]
                    print("RAFT - INFO: Log Inconsistency -> Entry at request.prev_log_index + 1 had a conflict with the corresponding entry in our log. Deleted that entry and all following")
            except Exception as e:
                print("RAFT - EXCEPTION PREVENTED : Leader log is longer than mine")
            
        # ACCEPT REQUEST
        self.logs.append(new_entry)
        print("My logs contain:")
        [print('\t-->', str(entry_inst)) for entry_inst in self.logs]

        self.execute_multiple_commits(request.leader_commit)
        # if request.leader_commit > self.commitIndex:
        #     self.commit_entry()
        #     self.commitIndex = min(request.leader_commit, self.get_last_log_index() - 1)

        return node_connection_pb2.AppendEntriesResponse(term=self.term, success=True)
    
    # compares a value for this node to a value from a candidate's node
    # fn: a function that can return us a value from this node (self.get_last_log_term or maybe self.get_last_log_index)
    # candidates_value: value from candidate node that we would like to compare to our own value
    # return: a CandidateValueComparison enum which tells us how the candidate's value compares to our own
    def get_candidate_comparison(self, fn: Callable, candidates_value: int) -> CandidateValueComparison:
        our_val: int = fn()
        if our_val == candidates_value:
            return CandidateValueComparison.CANDIDATE_VALUE_SAME
        
        return CandidateValueComparison.CANDIDATE_VALUE_GREATER if our_val < candidates_value else CandidateValueComparison.CANDIDATE_VALUE_LESSER

    """
    End: Implementation of RPC Service interface
    """

    """
    Start: Implementation of RPC Client functions
    """
    # entry: information about the operation we are replicating across state machines
    # prev_log_index: index in Node.logs of operation immediately preceding those we are replicating
    # prev_log_term: term of operation at Node.logs[prev_log_index]
    def fan_out_append_entry_rpcs(self, entry: Entry, prev_log_index: int, prev_log_term: int) -> int:
        successes: int = 0
        for _, stub in self.peer_stubs.items():
            result: bool = self.append_entryRPC(stub, entry, prev_log_index, prev_log_term)
            if result:
                successes += 1
        
        return successes

    # return: True if the appendEntry rpc was successful,False otherwise
    # TODO: we should add a retry mechanism
    def append_entryRPC(self, stub, entry: Entry, prev_log_index: int, prev_log_term: int) -> bool:
        """If the Node is a leader, and it receives a message from a client, it tries to append that entry to the follower nodes
        """
        try:
            # NOTE: For testing purposes do another append entry if you're a leader
            response = stub.appendEntries(
                node_connection_pb2.AppendEntriesRequest(
                    term=self.term,
                    leader_id=self.port,
                    prev_log_index=prev_log_index, 
                    prev_log_term=prev_log_term, 
                    entries=[entry.operation],
                    leader_commit= self.commitIndex
                )
            )

            if response.term > self.term:
                self.update_state(NodeState.FOLLOWER)

                return False

            return response.success
        except:
            return False

    def send_heartbeat(self, stub: node_connection_pb2_grpc.NodeConnectionStub, stub_id: NodeId) -> None:

        try:
            response = stub.appendEntries(
                        node_connection_pb2.AppendEntriesRequest(
                            term=self.term,
                            leader_id=self.port,
                            prev_log_index=self.get_last_log_index(), 
                            prev_log_term=self.get_last_log_term(), 
                            entries=[], # empty append entry, just a heartbeat
                            leader_commit= self.commitIndex
                        )
                    )
            # print(f"RAFT - INFO : Hearbeat -> Node {stub_id[0]}:{stub_id[1]} responded. Success: {response.success}")

            if response.term > self.term:
                self.update_state(NodeState.FOLLOWER)
        except Exception as e:

            print(f"RAFT - INFO : Hearbeat -> Node {stub_id[0]}:{stub_id[1]} was unreachable. Likely down!")
        finally:
            return

    # what happens if a node wins two elections in rapid succession?
    # --> could this result in multiple calls to send_heartbeats and thus multiple calls to reset()?
    def send_heartbeats(self) -> None:
        for id, stub in self.peer_stubs.items():
            if self.state == NodeState.LEADER:
                self.send_heartbeat(stub, id)
        
        if self.state == NodeState.LEADER:
            self.heartbeat_timer.reset()

    def start_election(self) -> None:
        """When the election timer runs out, start an election. Also will happen if it doesn't receive a heartbeat from the leader
        """
        # if already leader, don't run the election
        if self.get_status().data["role"] == "Leader": # idk why but this isn't being read properly NodeState.LEADER:
            self.send_heartbeats()
            return None

        # If you are single node set yourself as leader
        
        self.update_state(NodeState.CANDIDATE)
        # Setup for vote request
        self.term += 1

        # We need to be very careful how we increment the term during elections.
        # consider the case where a new election starts before this one is finished. If we were to simply send self.term in the rpc,
        # then this function could end up sending requestVote's with varying terms, but aggregating their results as if they're from the same
        # term. To avoid this, store the term in a tmp variable and send that in the rpc, not self.term.
        term_for_vote: int = self.term
        votes: int = 1 # vote for myself
        self.election_timer.reset()
            
        # TODO: ensure these rpc calls are non-blocking
        last_log_index: int = self.get_last_log_index()
        last_log_term: int = self.get_last_log_term()
        for voter_id, stub in self.peer_stubs.items():
            # print(f"{datetime.datetime.now()} RAFT - INFO : Requesting Vote\nTerm: {term_for_vote} | ID : {self.port} | Last Log Index : {last_log_index} | Last Log Term : {last_log_term}")
            address: str = f'{voter_id[0]}:{voter_id[1]}'
            try:
                response = stub.requestVote(
                node_connection_pb2.VoteRequest(
                    term=term_for_vote, 
                    candidate_id=self.port, 
                    last_log_index=last_log_index,
                    last_log_term=last_log_term
                    ))

                # print(f'{datetime.datetime.now()} RAFT - INFO: Vote response received! Id: {address} | Term: {response.term} | Vote Granted: {response.vote_granted}')

                votes += response.vote_granted
            except Exception as e:
                print(f'{datetime.datetime.now()} RAFT - INFO: VoteRequest RPC Call to {address} failed! {address} likely')

        # election won
        if votes >= self.get_minimum_vote_for_majority():
            self.update_state(NodeState.LEADER)

            print(f"\nNode {self.port} says: \nLOOK AT ME ... I AM THE CAPTAIN NOW")
            # print("RAFT - INFO : Sending heartbeat")

            # immediately send heartbeats to establish our authority and notify other nodes. start timer if it's not already running
            self.send_heartbeats()

        # if election not won, then do nothing. Wait for your timer to time out and reset it
        # or wait to get a valid append entry from a leader
    """"
    End: Implementation of RPC Client functions
    """

    """
    Start: Interface to Flask API
    """
    # adds the topic. If an error occurs, will set NodeOperationResult.success to False and apply an appropriate error message 
    def put_topic(self, topic: str) -> NodeOperationResult:
        if not self.is_leader():
            return NodeOperationResult(False, ErrorCode.LEADER_ERROR)
        
        if topic in self.topics:
            return NodeOperationResult(False, ErrorCode.REQUESTED_TO_ADD_TOPIC_WHICH_ALREADY_EXISTS_ERROR)

        """ START OF WHAT I SEND THE FOLLOWERS """

        operation: node_connection_pb2.OperationDetails = node_connection_pb2.OperationDetails(operation=Operation.PUT_TOPIC.value, topic=topic, message="")
        entry: Entry = Entry(self.term, operation)
        should_commit, num_successes = self.send_to_followers(entry)
        print(f"RAFT - INFO: shared PUT_TOPIC operation with all nodes. Received {num_successes} successful responses. Committing: {should_commit}")
       
        """ END OF WHAT I SEND THE FOLLOWERS """
        if should_commit:
            self.logs.append(entry)
            self.commitIndex +=1

        # TODO: how do we make this thread safe? Imagine the following scenario:
        # 1) put_topic() is called on this thread (main thread)
        # 2) no topic is found, so we need to create one
        # 3) new topic is created with an empty queue
        # What happens if inbetween steps (2) and (3), the RPC thread invokes appendEntries(), resulting in a 
        # new empty FIFO queue being created for this topic. This queue would then subsequently be overwritten (to an empty queue) by step (3).

        else:
            return NodeOperationResult(success=False, error=ErrorCode.MISC_ERROR) # return misc error for now -- followers rejected the request for some reason


        self.topics[topic] = Queue()    
        return NodeOperationResult(True)

    # gets the topics
    def get_topics(self) -> NodeOperationResult:
        if not self.is_leader():
            return NodeOperationResult(success=False, error=ErrorCode.LEADER_ERROR)

        """ START OF WHAT I SEND THE FOLLOWERS """

        operation: node_connection_pb2.OperationDetails = node_connection_pb2.OperationDetails(operation=Operation.GET_TOPIC.value, topic="", message="")
        entry: Entry = Entry(self.term, operation)
        should_commit, num_successes = self.send_to_followers(entry)
        print(f"RAFT - INFO: shared GET_TOPIC operation with all nodes. Received {num_successes} successful responses. Committing: {should_commit}")
       
        
        """ END OF WHAT I SEND THE FOLLOWERS """
        
        if should_commit:
            self.logs.append(entry)
            self.commitIndex +=1

            # Here, the leader can actually perform the operation that the client asked them to do. They don't need to actually commit the entry, they just need to make sure they increment their commitIndex to reflect that they performed the operation
            # I think that as a leader here we should not be calling self.commit_entry(). Just perform the command that was asked and increment the commit index. That way, when I send an AppendRPC to the nodes again, they'll know to commit
            # self.commit_entry()
            
        else:
            return NodeOperationResult(success=False, error=ErrorCode.MISC_ERROR) # return misc error for now -- followers rejected the request for some reason
        
        # Here, the leader responds to the client request with what they asked for
        return NodeOperationResult(success=True, error=None, data={"topics": list(self.topics.keys())})
        
            

    # adds a message to the appropriate queue by topic
    def write_message(self, topic: str, message: str) -> NodeOperationResult:
        if not self.is_leader():
            return NodeOperationResult(False, ErrorCode.LEADER_ERROR)
        
        if not topic in self.topics:
            return NodeOperationResult(False, ErrorCode.REQUESTED_TO_WRITE_MESSAGE_TO_TOPIC_THAT_DOES_NOT_EXIST_ERROR)

        """ START OF WHAT I SEND THE FOLLOWERS """

        operation: node_connection_pb2.OperationDetails = node_connection_pb2.OperationDetails(operation=Operation.PUT_MESSAGE.value, topic=topic, message=message)
        entry: Entry = Entry(self.term, operation)
        should_commit, num_successes = self.send_to_followers(entry)
        print(f"RAFT - INFO: shared PUT_TOPIC operation with all nodes. Received {num_successes} successful responses. Committing: {should_commit}")
       
        """ END OF WHAT I SEND THE FOLLOWERS """
        if should_commit:
            self.logs.append(entry)
            self.commitIndex +=1
        
        else:
            return NodeOperationResult(success=False, error=ErrorCode.MISC_ERROR) # return misc error for now -- followers rejected the request for some reason


        self.topics[topic].put(message)
        return NodeOperationResult(True)

    # retrieves a message from the server from the provided topic
    def get_message(self, topic: str) -> NodeOperationResult:
        if not self.is_leader():
            return NodeOperationResult(False, ErrorCode.LEADER_ERROR)
        
        if not topic in self.topics:
            return NodeOperationResult(False, ErrorCode.REQUESTED_MESSAGE_FROM_TOPIC_THAT_DOES_NOT_EXIST_ERROR)

        if self.topics[topic].empty():
            return NodeOperationResult(False, ErrorCode.QUEUE_EMPTY_ERROR)
        
        """ START OF WHAT I SEND THE FOLLOWERS """

        operation: node_connection_pb2.OperationDetails = node_connection_pb2.OperationDetails(operation=Operation.GET_MESSAGE.value, topic=topic, message="")
        entry: Entry = Entry(self.term, operation)
        should_commit, num_successes = self.send_to_followers(entry)
        print(f"RAFT - INFO: shared PUT_TOPIC operation with all nodes. Received {num_successes} successful responses. Committing: {should_commit}")
       
        """ END OF WHAT I SEND THE FOLLOWERS """

        if should_commit:
            self.logs.append(entry)
            self.commitIndex +=1

        return NodeOperationResult(True, error=None, data={"message": self.topics[topic].get()})

    def get_status(self) -> NodeOperationResult:
        return NodeOperationResult(True, error=None, data={"role": self.state.value, "term": self.term})

    """
    End: Interface to Flask API
    """

def main() -> None:
    args = sys.argv
    validation = parseAndValidateServerLaunchArguments(args)

    if not validation[0]:
        print(f'Error while parsing launch command. Exiting.\nError: {validation[1]}')
        return

    # list of tuples with info on our peer nodes like [(ip 1, port 1), (ip 2, port 2), ...]
    # we will make an RPC connection to each of these facilitating message exchange in support of our distributed message queue
    peers: PeerList = []
    
    fields = validation[1]
    id = fields["this_server_index"]
    servers = fields["servers"]

    for index, server in enumerate(servers):
        ip: str = server["ip"]
        rpc_port: int = server["internal_port"]

        if index == id:
            # we are guaranteed to enter here because the validation ensured the index was in the bounds of the server array
            my_ip: str = ip
            my_flask_port: int = server["port"]
            my_rpc_port: int = server["internal_port"]
        else:
            peers.append((ip, rpc_port))

    node = Node((my_ip, my_rpc_port), peers)

    flask_api_server: flask_server.FlaskServer = flask_server.FlaskServer(my_ip, my_flask_port, node)
    flask_api_server.startFlaskServer()

if __name__ == "__main__":
    # To Run: python3 src/node.py <path_to_config> <index>
    main()