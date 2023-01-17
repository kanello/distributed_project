# Technical Report

## Message Queue

- The structure of our distributed message queue was for a Flask Server to field requests from a client and for that Flask Server to also have access to a Node object which stores and manages the state of the distributed message queue using the Raft consensus algorithm. As client requests are received by the Flask Server, the server calls methods on the Node object to access the message queue and disseminate those requests to other nodes in our distributed message queue network. We decided to structure our application like this because it provided a strong degree of encapsulation for the Message queue with all state and communication between nodes being handled exclusively within the Node class.
- Each new topic created is a python queue.Queue, maintains FIFO order, adding items with put() and getting items using get(). We did not want to implement a FIFO queue from scratch.
- A dictionary inside of the Node object called topics, stores the topics as a key and their Queue as a value.
- Each Node object also stores a list with the logs of the operations it should execute on the message queue as well as a commitIndex which tracks the index of the last operation which has been executed on that Node’s message queue.
- When the client tries to interact with the message queue, the following takes place
  - Add a topic → the leader appends the operation to their logs, reaches out to its followers and if a quorum is reached, it commits the operation, adding a `queue.Queue` with `topic_name` to the `topics` dictionary.
  - Get a topic →
  - Add a message → find the topic and then `queue.Queue.put(message)`
  - Get a message → find the topic and then `queue.Queue.get(message)`
- To reach a quorum, a majority of the followers need to respond that they accept the entry. Once they do, the leader increments their commit index and performs the requested operation
- In the next append entry made, the followers commit any uncommitted entries after their last commit and before the leader’s commit index
- The nodes communicate with each other using protobuf.

## Election

- The start of elections are dictated by a ResettableTimer which is a class that wraps Python’s threading.Timer class. The core idea behind the election timer is that a node should start an election if enough time has gone by without receiving a valid heartbeat. It then follows that every time a valid heartbeat is received or an election is started, we reset the election timer.
- An election involves a node making an RPC call to every other known node in the network requesting their vote. In this call, the requesting node shares its details such as its term, address, last log term, and last log index. The receiving nodes can then incorporate this information when deciding whether or not to grant their vote for the requesting node. The requesting node tallies up the positive vote responses and transitions to become leader if a quorum is achieved. When this happens, the new leader immediately sends heartbeats to all nodes to establish its leadership.
- This node will remain as leader until it receives a valid heartbeat from another node indicating a new leader has taken over.

## Fault Tolerance

- If a leader dies, then the followers will not receive a heartbeat to reset their election timer, thus triggering a new election in at least 200 ms and at most 300ms from the previous heartbeat. If no AppendEntryRPC is made, heartbeats should go out every 199 ms, in order to ensure no election is triggered when it does not need to be.
- One point to make here is that we did not know how often a heartbeat should go out, so we just made sure it was slightly quicker than the election timer
- It is important to think through how to handle RPC calls in the case when nodes go down. At a first level, we wrap all RPC calls in a try/except block to prevent the calling thread from crashing. Secondly, we continue to issue VoteRequest and AppendEntry requests to all nodes, even if we have previously encountered a failed RPC call. This ensures that if a node goes down, then comes back online, they can still participate in leader election and receive operations from the leader in regards to the message queue.

---

## Sources

- Raft paper
- Max’s implementation
- Raft website
- The secret lives of data raft visualization
- Also, asked chat GPT questions

## Short Comings

- We did not implement the nextIndex and matchIndex components of the raft Nodes and we made up for it by forcing Nodes to commit any uncommitted logs between their last commit and the leader’s last commit. We made sure to only do this after the node has compared its logs against the leader’s and reconciled differences.
- When we make RPC calls (AppendEntry and VoteRequest), we iterate over nodes in the system and make one call to each. Currently, these are each blocking calls, meaning that after making an RPC call to node A, we won’t make a call to node B until node A’s response comes back. Although we were aware the Raft paper specifies non-blocking RPC calls, since all of our nodes are on the same ip address (localhost), we thought the latency due to network calls would be negligible. In particular, AppendEntry calls which are heartbeats will benefit greatly from making the calls non-blocking (at least in a system distributed across many servers). This is because as soon as a new leader is elected, it should immediately send heartbeats to all nodes in a non-blocking manner.
