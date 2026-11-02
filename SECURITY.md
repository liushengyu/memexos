# Security policy

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub:
[Security → Report a vulnerability](https://github.com/liushengyu/memexos/security/advisories/new).

Say what is affected, how to reproduce it and what an attacker could do. We aim to acknowledge reports within a week,
keep you informed while we work on a fix, and credit you in the advisory unless you prefer otherwise.

## Supported versions

MemexOS is in early development. Security fixes go into `main` and the next release; older versions are not patched.

## What is in scope

MemexOS runs on your own machine and handles your private material, so these matter most:

- the console: it must listen on 127.0.0.1 only, refuse foreign `Host` and `Origin` headers, and only serve files from
  the knowledge base's allowed folders;
- the sandbox around agent runs: writable and readable paths, allowed commands, network access;
- secrets: keys must come only from environment variables or the system keychain, and secrets found in captured
  material must be removed before it is written;
- fetching: private network addresses must be refused, redirects checked hop by hop;
- prompt injection: instructions inside captured sources must not be able to make an agent act outside its policy.

The model services you configure, and the security of the machine itself, are outside this project's control.
