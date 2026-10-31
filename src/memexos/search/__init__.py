"""Read-side primitives: search and get.

Backends (memexos.search.fts: built-in SQLite full-text; memexos.search.qmd:
vector + keyword search through the qmd CLI) produce candidate lists, which
are fused by rank (RRF) and optionally reordered by a reranker. Everything here
must work in a read-only sandbox.
"""
