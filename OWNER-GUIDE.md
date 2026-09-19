# Owner's Guide

A plain-language guide for the business owner running this system - no coding knowledge assumed.
If you're a developer, see [OPERATION.md](OPERATION.md) or [DEPLOYMENT.md](DEPLOYMENT.md) instead;
this guide deliberately leaves out anything you'd need a developer for.

## What this is

Your customers message a WhatsApp number to book an appointment. An AI assistant answers them,
checks your real calendar, and books the appointment - automatically, day or night. It runs on a
small server (an "instance") rented from Amazon Web Services (AWS), which needs to stay switched
on for the system to work.

## Checking that everything is working

The simplest check: **message your own business's WhatsApp number** from a different phone (or
ask someone else to) and ask something like *"what services do you offer?"*. You should get a
reply within about 30 seconds. If you do, everything is working - you don't need to read any
further unless something seems wrong.

## Logging into AWS

You'll have a **team code** (given to you separately) and a login link. If you don't have these,
ask whoever set this up for you.

1. Go to the login page (the link you were given).
2. Enter your **team code** as the username.
3. Check your email for a verification code from `no-reply@login.awsapps.com` and enter it.
4. Set up a security step called **MFA** (multi-factor authentication) the first time - this uses
   an app on your phone (or your phone's built-in authenticator) to confirm it's really you each
   time you log in. Follow the on-screen instructions; it only takes a minute.
5. Set a password.
6. You'll land on a page called **"Innovation Sandbox on AWS"**. If it says "My Leases (0)", ask
   your developer to check the lease is set up - you shouldn't need to request one yourself.
7. Once you see an active lease, click **"Login to account"**, then click the blue link under
   "Select a role" (it'll say something like `ISSISB_IsbUserPS`).

You're now in the AWS Console - the control panel for everything running your system.

## Checking on your server (the "instance")

1. In the AWS Console, use the search bar at the top and type **Lightsail**, then click it.
2. You'll see your instance listed (something like `agent42-demo`) with a status - it should say
   **Running** with a green dot. That's good - it means the server is switched on.
3. Click the instance name to see more detail, including its address (a set of numbers, e.g.
   `X.X.X.X`) - you won't usually need this yourself, but a developer helping you may ask for it.

## If something seems broken: try restarting first

If WhatsApp isn't replying and it's been more than a few minutes, a restart fixes most issues.
This is safe - the system is set up to bring everything back automatically after a restart,
including reconnecting to WhatsApp.

1. On the instance's page in Lightsail, find the **Stop** button (top right area, or under a
   menu depending on the page layout).
2. Wait for the status to show **Stopped** (can take a minute).
3. Click the same button again, now labelled **Start**.
4. Wait 2-3 minutes, then send a WhatsApp test message as above.

If it's still not responding after that, stop here and contact your developer (see below) rather
than trying further fixes yourself - the next steps genuinely need technical knowledge.

**Important:** stopping the instance does **not** stop it costing money - only deleting it does.
Don't delete it unless your developer tells you to; that's permanent and can't be undone.

## Connecting directly (only if a developer asks you to)

"SSH" is just the technical name for opening a command-line window directly on your server - like
remote-controlling it. You will only need this if a developer is walking you through something on
a call, or asking you to run a specific command they give you. There are two ways to do it:

**Easiest - no setup needed:** on the instance's page in Lightsail, click the button labelled
**"Connect using SSH"**. This opens a black text window directly in your browser, already
connected - nothing to install. Type exactly what your developer tells you to, then press Enter.

**Alternative - from your own computer's Terminal app:** this needs a small file called a "key"
(ends in `.pem`) that your developer can give you, or you can get it yourself:
1. In the AWS Console, click your account name (top right) → **Account** → **SSH keys** tab.
2. Find the key for the right region and click **Download**.
3. Give the downloaded file to your developer, or follow their instructions to use it from your
   own Terminal/Command Prompt app.

Either way: **never share this key file or post it anywhere public** (email is fine if sending
directly to your developer) - anyone who has it can access your server.

## Understanding costs

This runs on a paid AWS plan with a capped budget (set up as part of the hackathon). The main
ongoing cost is the server itself, billed for as long as it exists - whether it's running or
stopped. You can see current spending in the AWS Console under **Billing**, or ask your developer
to check the lease's remaining budget for you.

## When to contact your developer

Reach out rather than troubleshooting further if:
- A restart (above) didn't fix it.
- WhatsApp shows "linked" but replies never arrive, or arrive with clearly wrong information
  (e.g. wrong prices, wrong hours).
- You want to change what the assistant says, your business hours, services, or pricing - these
  are configuration changes a developer makes, not something to edit yourself.
- You're not sure whether something is safe to click - when in doubt, ask first rather than
  experiment on the live system.

## What you don't need to worry about

Everything else - the code, the database, deployments, updates - is handled by your development
team. You don't need to understand Docker, GitHub, or any of the other technical tools mentioned
in this project's other documentation. This guide covers everything you should need day to day.
