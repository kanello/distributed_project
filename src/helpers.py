"""
Module to provide helper functions such as parsing and verifying command line inputs
"""

import json
from queue import Queue
from typing import Any, Dict, List, Optional, Tuple, Union
from enum import Enum
import node_connection_pb2

import node_connection_pb2_grpc # type: ignore

"""
parses and validates the terminal command to launch a node server such as "python3 src/node.py path_to_config index" 
args: args passed to command line
returns: a 2-element tuple where the first element indicates success/failure and the second argument has parsed info.
for example: [True, <data for success case>] or [False, <message explaining failure>]
"""
def parseAndValidateServerLaunchArguments(args):
    if len(args) != 3:
        return [False, "launch command must contain 2 arguments like 'python3 src/node.py <path to config file> <index of this server's info in config file>'"]

    config_file_path = args[1]

    try:
        index_of_this_server_in_config = int(args[2])
        f = open(f'./{config_file_path}')
        config = json.load(f)

        # ensures the servers attribute exists and index is within the bounds of the servers array
        this_server = config["addresses"][index_of_this_server_in_config]
    except Exception as e:
        return [False, f'Encountered an error while retrieving the json file. Error: {e}']

    return [True, 
        {
            "this_server_index": index_of_this_server_in_config,
            "servers": config["addresses"]
        }
    ]

NodeId = Tuple[str, int] # id of a node is defined as a tuple like (<ip adddress>, <port>)
PeerList = List[NodeId]

class ErrorCode(Enum):
    LEADER_ERROR = 'leader_error' # you requested a follower to perform a leader-only action
    TOPIC_ERROR = 'topic_error' # generic error type for topic-related tasks like requesting to add a topic that already exists
    QUEUE_EMPTY_ERROR = 'queue_empty_error' # requested to get a message from a topic which has an empty queue
    REQUESTED_TO_ADD_TOPIC_WHICH_ALREADY_EXISTS_ERROR = 'requested_to_add_topic_which_already_exists_error'
    REQUESTED_MESSAGE_FROM_TOPIC_THAT_DOES_NOT_EXIST_ERROR = 'requested_message_from_topic_that_does_not_exist_error'
    REQUESTED_TO_WRITE_MESSAGE_TO_TOPIC_THAT_DOES_NOT_EXIST_ERROR = 'requested_to_write_message_to_topic_that_does_not_exist_error'
    COULD_NOT_RESOLVE_STATE = 'could_not_resolve_state'
    MISC_ERROR = 'miscellaneous_error'

# represents how a candidate's value compares to ours.
# this is useful in a number of cases like term or log index comparison
class CandidateValueComparison(Enum):
    CANDIDATE_VALUE_GREATER = 'candidate_value_greater' # example: candidate's term: 100, our term 98
    CANDIDATE_VALUE_SAME = 'candidate_value_same'
    CANDIDATE_VALUE_LESSER ='candidate_value_lesser'

class NodeState(Enum):
    FOLLOWER = "Follower" # formally 0
    CANDIDATE = "Candidate" # formally 1
    LEADER = "Leader" # formally 2

class Operation(Enum):
    GET_TOPIC = "get_topic"
    PUT_TOPIC = "put_topic"
    GET_MESSAGE = "get_message"
    PUT_MESSAGE = "put_message"
    GET_STATUS = "get_status"

# provides structure to results of operations in the Node class that are called by the flask API
class NodeOperationResult:
    def __init__(self, success: bool, error: Optional[ErrorCode] = None, data: Optional[Dict[str, Any]] = None) -> None:
        self.success: bool = success
        self.error: Optional[ErrorCode] = error
        self.data: Optional[Dict[str, Any]] = data

class Entry:
    def __init__(self, term: int, operation: node_connection_pb2.OperationDetails) -> None:
        self.term: int = term
        self.operation: node_connection_pb2.OperationDetails = operation

    def __str__(self) -> str:
        return f'Entry Object - term: {self.term}, operation: {self.operation.operation}, topic: {self.operation.topic}, message: {self.operation.message}'

Logs = List[Entry]
TopicsDict = Dict[str, Queue]
PeersDict = Dict[NodeId, node_connection_pb2_grpc.NodeConnectionStub]