# Contributions Report

Max Zinski: mzinksi@uchicago.edu
Anthony Kanellopoulos: akanello@uchicago.edu

Our approach to the project was for each of us to initially work on a distinct component to get it working properly. Then, once a component appeared to be working as expected, we integrated those components together and began testing the system as a whole and working together on whichever parts needed additional work. For example, Anthony initially setup the Flask API server and endpoints while Max setup an example of RPC using VoteRequest. Then, Max flushed out the Flask API while Anthony implemented the baseline Raft Framework. At this point, we had a working Flask Server that maintained a reference to a Node Object which implemented the Raft algorithm and maintained the state of the message queue. Then, we worked together to refine and test various aspects as needed throughout the system.

Anthony: initial Flask API server, implemented version 1.0 of Raft algorithm which elected a leader and sent various other communications
Max: initial RPC example, bolstered Flask API Server by adding type safety and connecting flask to Node
Anthony and Max: refined and optimized our initial version of Raft, made sure Flask API was returning proper data and status codes, and made various extensions of Node to handle whatever else was needed.
