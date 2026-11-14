"""Setup (platform/CONTRACT.md section 10): the machine profile's fields, the lookup
that picks a library entry from it, and the setup agent that fills the fields.

platform/ is not a package (it would shadow the stdlib `platform`): put platform/
on sys.path and import `setup.lookup` or `setup.agent`.
"""
