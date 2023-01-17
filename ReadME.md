# Overview of App and Components
* Each server contains:
    * flask server allowing clients to request messages, post messages to topics, etc.
    * rpc server allowing other nodes to communicate with it for purposes such as requesting votes and sharing log entries
    * a collection of client stubs (Node.peer_stubs), each representing the rpc connection between the server and its peers. Peers can be interacted with by calling functions on its client stub.
* Each server has two different ports, one for its rpc server and one for its flask server. These ports are defined in the server_config.json file. When launching a server via the command line, we specify the index of the associated config info for this server.

# RPC Notes and Running Application
* /src/protos/node_connection.proto defines our NodeConnection service. This defines the interface to our node servers, including the response/request message types. Any new functions that we add to the proto file should be implemented on the Node class.
* Compiling the .proto file will generate two python files with the service implementation. 
    * node_connection_pb2_grpc.py: client and server classes
    * node_connection_pb2.py: request/response message types
* Whenever updates are made to node_connection.proto, these files will need to be regenerated. This can be done by executing the following command: 'python -m grpc_tools.protoc -Isrc/protos --python_out=src/ --pyi_out=src/ --grpc_python_out=src/ src/protos/node_connection.proto'
* Example usage of rpc calls can be found in Node.start_election(). This iterates over the stubs (known peers) and calls the requestVote() method on each. Node.requestVote() is the server implementation for this call.
* To test out the example rpc calls, open four terminals, navigate to this project's home directory, and execute these four commands:
    * python3 src/server.py server_config.json 0
    * python3 src/server.py server_config.json 1
    * python3 src/server.py server_config.json 2
    * python3 src/server.py server_config.json 3
    * I recommand copy/pasting the commands into all four terminals before executing any. Once commands are executed, each server will initiate an election after 8-10 seconds. If any server isn't running by the time the election starts, I'm pretty sure an error will be thrown because we will try to make an rpc call to a server that hasn't started yet.

# Misc. Notes
* if you encounter errors with mypy related to protobufs not being typed correctly, execute 'python3 -m pip install types-protobuf'
Instlaling types-protobuf fixed this error for me: node_connection_pb2.pyi:1: error: Library stubs not installed for "google.protobuf.internal"

* Not a big deal for now - I am still seeing a mypy error from node_connection_pb2_grpc.py's import of grpc. This error can probably be fixed by either altering the grpc compile command (specifying to type ignore modules) or by loosening the strictness of our mypy check by adding flags to the mypy command.