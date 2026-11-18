# Saarthi

Saarthi connects a distributor’s field sales team with its office and Tally accounting system. It is used by a real business. This public showcase contains anonymized examples and a fresh Git history.

## The use case

Distributors take orders from shops across multiple routes. Salespeople need stock availability, customer purchase history and selling prices during each visit. The office needs those orders in Tally, while the owner needs visibility into billing and field activity.

Saarthi connects these steps through a mobile website, a Windows Admin application and a background synchronization agent.

- **Salespeople** open a customer’s account, check previous purchases and stock, enter rates and quantities, record visits, and submit orders from their phones. Saved drafts and a retry flow help recover interrupted submissions.
- **Office staff** review orders and invoices, manage selling rates and product images, control which products appear online, and synchronize orders with Tally for billing.
- **Owners** review sales, invoices, customer activity and team visits remotely. Owner access also includes purchase rates and controls to pause or resume laptop synchronization.

For example, during a shop visit, a salesperson can find the customer’s usual products, use the configured selling rate or available last sold price, and submit the order. The laptop agent picks it up for Tally processing and publishes updated billing information back to the website. Orders await pickup while the laptop is offline or synchronization is paused.

## Try the demo

```sh
python -m http.server 8000 --directory web
```

Open **http://localhost:8000/?demo=1** to explore the sample ordering workflow. The demo uses illustrative data and runs without a Tally or cloud database connection.

## Technology

Python · FastAPI · JavaScript · HTML/CSS · SQLite · PostgreSQL · Neon · Railway · Tally XML integration

## Explore the implementation

Read the [architecture](ARCHITECTURE.md), [Windows Admin guide](DESKTOP_ADMIN.md), or [deployment and update guide](DEPLOYMENT_AND_UPDATES.md). Configuration examples are in `.env.example`.

Live integrations require your own credentials and Tally setup. The root integration test scripts can change connected records, so run them against a test environment. Production credentials, business databases and private signing keys are excluded from this showcase.
