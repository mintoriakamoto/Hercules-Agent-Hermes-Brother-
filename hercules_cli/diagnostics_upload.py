"""Diagnostics upload client (removed).

This module previously implemented the opt-in (``--nous``) destination for
``hercules debug share``, uploading gzipped debug bundles to a Nous-owned S3
bucket via a short-lived signed URL minted by the Nous account service. The
Nous Portal provider has been removed, so this upload path no longer exists.

``hercules debug share`` now uploads only to the public paste path (or prints
locally with ``--local``).
"""
