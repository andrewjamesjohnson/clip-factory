"""The clip factory: ten clips a day per account, made overnight by a Hermes bot, signed off by a person, then scheduled.

Plumbing only. The clips themselves are made by the account's own skill (gaming, viral-clip-engine); the factory
plans the day's slots, collects what the skill delivered, builds the drafts, records the sign-off and hands approved
posts to the outlets.
"""
__version__ = "0.1.0"
