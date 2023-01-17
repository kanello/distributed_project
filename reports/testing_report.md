# Testing Report

- We did most of our testing by spinning up instances of the nodes, watching them run elections (at slower intervals) and then via a client python file (or sometimes Insomnia) performing operations on the message queue of the nodes.
- We tried killing a leader after entries were added to the message queue and then requesting messages from the queue from the next leader
- Our nodes contained many debugging statements, where they just printed to the terminal
  We also ran Max’s test suite at each part of the process
