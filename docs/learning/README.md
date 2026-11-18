# Saarthi system design for product managers

Open [Design Saarthi from scratch](../../.archify/explainer-saarthi-product-design-20261105-150935/saarthi-product-design.html). The standalone webpage covers the design as a learning exercise and describes the system's behavior at each handoff.

1. Define the people, problem, scope and first release.
2. Start with order capture and an office review step.
3. Add shared storage, Tally billing, synchronization and recovery.
4. Describe the high-level design through components, boundaries and record ownership.
5. Describe the low-level design through inputs, rules, decisions, states and exceptions.
6. Practice explaining the design through scenarios and proposed product measures.

The first-release sketch is a teaching example. Use the order and failure walkthroughs for Saarthi's behavior.

For the operational design, follow the [low-level design walkthrough](low-level-design.md) from website submission to billing and synchronization. Use the [database field reference](database-fields.md) to trace the records in PostgreSQL and both local SQLite files.

The [API contract reference](api-contracts.md) lists inputs and outputs by route. [Hosting, retention and ownership](data-lifecycle.md) explains Neon, the seven-day cleanup rules, and which data is copied to the office PC.
