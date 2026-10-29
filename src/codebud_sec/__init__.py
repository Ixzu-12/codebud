"""
codebud_sec

The security layer of codebud (phase 10): profiles and records for
authorized, defensive security work -- incident response on hosts the
operator owns, or a penetration test the operator is contracted to run.

It is a separate layer over the codebud core, kept apart so that it can
later be packaged or moved on its own:

- codebud_sec imports only the core API listed in tests/test_layers.py;
  widening that list is a deliberate decision, made in that test.
- The codebud core never imports codebud_sec.

General privacy features the security profiles switch on (encryption at
rest, model and tool integrity checks, the no-egress lock) live in the
core, where every user can have them.
"""
