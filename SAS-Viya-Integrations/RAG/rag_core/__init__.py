# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""sas rag_core — shared Python for the RAG ingestion steps and retrieval runtime.

Distributed as a SAS Content folder (/Public/SAS-Agentic-AI/RAG/rag_core/) that
the custom steps download and sys.path.insert — never as a pip package. Keep it
dependency-light: stdlib everywhere, `requests` in scr.py, `psycopg2` imported
lazily inside the pgvector adapter, `pypdfium2` lazily inside the pdf extractor.

Steps log RAG_CORE_VERSION at startup so every run records the code it ran with.
"""

RAG_CORE_VERSION = "0.1.0"
