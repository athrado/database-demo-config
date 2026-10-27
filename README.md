# Database demo config

## In a Nutshell

**What it is.** A shared demo database with a small web app to look at it and change it. Every demo (banking, insurance, retail, telco, medical, IT, starter) has its own set of
data: customers and the records that belong to them (accounts, orders, claims, cases...). Teneo solutions read and write the same data during a conversation, so **what you change in the app is
what the Teneo solution sees**, and the other way round.

**In the app** (Streamlit)
- Pick a **dataset** in the sidebar, for example *Banking*. **Browse** shows a customer with everything linked to them; **Tables** shows the raw records; **Edit records** lets you change, add or delete a record.
- Each dataset is saved as a **template**: its **seed data** (the starting records) plus its **config** (title, icon, which table it uses, how it is shown). The sidebar buttons move data between the template and the **database** (the table Teneo reads):
  - **Update database**: template → database (adds the template's records).
  - **Reset database**: back to the seed data. Use it after a demo to undo the changes made during it so you replay the same scenario/use case.
  - **Update seed data**: database → template. Saves what is in the database now as the new starting point (and commits it to GitHub).
  - **Refresh database**: reads the database again (and picks up templates that are on GitHub but not here yet).
  - **Add New Dataset** / **Delete template**: make a new demo dataset, or remove one (it asks first).
- **Storage Connections** shows where the data lives (Azure) and the GitHub repository the templates are saved in.

**In Teneo** (through MCP). The data is served by an MCP server with 17 tools: find a customer, read or change a record or a single field, add or remove list entries,
change or delete several records at once, replace or clear a collection, and **reset** to the template. Delete-type tools only show a preview until you pass `confirm = true`.
To use them in a Teneo solution, add an **MCP toolkit**:

| Setting | Value |
|---|---|
| Server URL | `https://teneo-mcp-server.blackisland-1f803fbc.westeurope.azurecontainerapps.io/mcp` |
| Auth header name | `X-API-Key` |
| Auth header value | `<key>:<dataset>`, for example `<key>:banking` |

- The **key** is a shared secret: **ask Julia** for it, and enter it only in the toolkit's connection settings, never in a flow or a document.
- The part **after the colon** is the name of the dataset (the database), e.g. `banking`, `retail`, `insurance`. It decides which demo's data the tools work on, so use one toolkit per demo and the tools can't touch another demo by mistake.
- **Example:** the Teneo solution **MCP Demo** has such a toolkit (*Banking Database*) and two small flows that use it. Say `test database contact` (look up a customer and change their contact preference) or `test database payments` (find payments, flag them, and get the receipts) in its live test. Open the flows to see how the tools are called from *Use tool* nodes.
- All tools and their inputs are described in the README of the `teneo-mcp-server` project.

As long as you are using the Streamlit app, no database can be broken or deleted permanentely as everything is backed up and version-controlled in Github, so feel free to play around.

Access to Azure Tables or Github repo can be granted by Julia - but it is generally not required for basic use of this tool.

The rest of this README explains how the entire setup works in more detail. It's not required knowledge when simply using the app and the MCP tool.

## Overview

A small shared database for demos: **one table per demo, ready-made configs to load into it, a Streamlit
app to look at the data and change it, and a Teneo solution that reads and writes it.** Copy the folder,
point it at your own storage account, and you have the same setup for a new demo.

```
 Streamlit app (app.py)  ──┐                          ┌── table demoretail    ◄── Teneo solution A (secret: table = demoretail)
 seed.py (command line)  ──┼──►  Azure Table Storage ─┼── table demotelco     ◄── Teneo solution B (secret: table = demotelco)
 your own scripts        ──┘     account               └── table demobanking   ◄── ...
```

Everything talks to the storage account directly over HTTPS with a **SAS token**. There is no server of your
own to run, host or expose (no ngrok), so a cloud Teneo engine can reach the data.

## The data model

- A **table** holds the data of one demo.
- **Collections** group records inside a table: `customers`, `orders`, `claims`, ...
- A **record** has an **id** (`C001`) and is any **JSON object**. Nested objects and lists are fine.
- Records that belong to a customer carry that customer's id in a `customer_id` field. The app uses it to show
  a customer's orders, claims or tickets under the customer.
- Limits: ids can't contain `/ \ # ?`; one record can be up to 32,000 characters of JSON.

In Azure, one record is one table row: `PartitionKey` = collection, `RowKey` = record id, and the record as JSON text
in a `json` column. You can see and edit rows in the Azure portal (Storage browser) too.

## Files

| File / folder | What it is |
|---|---|
| `datasets/` | The ready-made demo data. One folder per config (see below). |
| `app.py` | The Streamlit app: browse, switch tables, load or reset a config, edit records. |
| `seed.py` | Command line: load a config into a table, export a table. |
| `store.py` | The storage layer (Azure Table Storage, plus local files for trying things out). |
| `datasets.py` | Loading, resetting and snapshotting configs. |
| `github_sync.py` | Commits files to GitHub through the API, used by Update seed data (section 8). |
| `check_datasets.py` | Checks every config; CI runs it. |
| `.github/workflows/tests.yml` | GitHub Actions: runs the tests on every push. |
| `render.py` | Turns any record into cards with icons and badges. |
| `make_documents.py` | Makes sample PDFs and a documents page per customer, for hosting in Blob Storage (section 7). |
| `teneo/ExtDataStore.groovy` | The Groovy client the Teneo solution uses (same data layout as `store.py`). |
| `teneo/tools/*.groovy` | The bodies of the seven toolkit tools: `read_record`, `list_records`, `find_records`, `load_customer_context`, `write_record`, `set_field`, `delete_record`. |
| `test_*.py` | Tests. The Azure code is tested against the free Azurite emulator, not real Azure. |
| `.env.example` | Configuration template. Copy to `.env`. |
| `.streamlit/config.toml` | Streamlit settings: the dark theme. |

## 1. Try it locally (no Azure needed)

```bash
pip install -r requirements.txt
python seed.py retail            # loads the retail config into a local file (data/local_store.json)
streamlit run app.py
```

With nothing configured, the app keeps each table in a local file under `data/`. The sidebar tells you which
storage is in use.

## 2. Configs

A config is a folder in `datasets/`:

| Config | Table it usually goes into | What's in it |
|---|---|---|
| 🌱 `starter` | `demodb` | 3 customers and their orders. Matches the example flow; the smallest set. |
| 🛡️ `insurance` | `demoinsurance` | Policy holders and families (Sophie Berthier, Sophie Bertram, ...), claims, requests, quotes. |
| 🛒 `retail` | `demoretail` | Customers with loyalty tiers, orders with items and tracking, returns, delivery issues. |
| 📶 `telco` | `demotelco` | Customers, tariffs and lines, invoices (one overdue), network issues, tariff requests. |
| 🏦 `banking` | `demobanking` | Four use cases. (1) Proactive loan offer: accounts with a 3-month average balance (below 300 EUR for Elena Bianchi), the low-balance rule, alerts, ready-made loan offers (5,000 / 7,500 / 10,000 EUR over 24 or 36 months at 6% APR), loan requests, and customers who must not get an offer. (2) Finding documents: 29 `transactions` for three customers, each with its own receipt link, built to test 1 result, 2 to 5 results and more than 5, with older and messy ones (see below). (3) Password reset by phone: customers with a blocked or active online banking password, masked identity data for the security interview, an interview log and the backend reset history (see below). (4) Several customer matches: Elena Bianchi (C001) and Elena Maria Bianchi (C006) share a first and a last name, so a search for "Elena" or "Bianchi" finds both. Italian names, addresses, phone numbers and IBANs. |
| 🩺 `medical` | `demomedical` | Patients, appointments, prescriptions, reimbursement claims, lab results. Fictional. |
| 💻 `it` | `demoit` | Employees, tickets, laptops and phones, access and hardware requests. |

All people, numbers and companies are made up. Every config's `customers` collection has `name`, `email`, `phone`,
`plan` and `preferences.contact_by`, so the example Teneo flow works on any of them.

**Template, seed data and config.** A dataset is saved as a **template**: a folder `datasets/<name>/` holding the **seed data** (`data.json`, the records) plus the **config** (`config.json`: title, icon, which table it uses; `view.json`: how the app shows it). The **database** is the Azure table the app and Teneo read.

**Two things, two directions.** The template (its seed data) and the database. The sidebar buttons say which one they touch:

| Button | Direction | What it does |
|---|---|---|
| **Update database** | seed data → database | Writes the seed records into the table, adding or overwriting. Records you added yourself stay. Never removes anything. |
| **Update seed data** | database → seed data | Overwrites the config's `data.json` with what is in the table now (asks first, shows how many records are added, removed or changed). Dates written as `{{today-7d}}` stay that way where nothing changed, links to documents are stored without their access token, and a copy of the old file is kept as `data.backup.json`. Use it to make your tweaks the new starting point. |
| **Reset database** | seed data → database | Asks "Are you sure you want to reset ... to its seed data?". On Yes it empties the **whole table** and loads the seed data again, so collections left over from an older version of the config are removed too (the pop-up lists them, and the sidebar warns when there are any). If another config uses the same table, only this config's own collections are emptied. The seed data is not changed. |
| **Delete template** | | Asks first (see below): removes the template (seed data + config), and optionally the data in the table. |
| **Refresh database** | | Reads the table again. |
| **Add New Dataset** | | Opens a form: a name, a table name, and the data as JSON. Write or paste your own, or start from a copy of an existing config. It creates the config (in `datasets/`) and its table. |

Each config has its own table (its `default_table`), so picking a config also picks the table.

- **Delete template** asks first and explains the difference: *the template* is the seed data plus the config (the folder in `datasets/`); *the data* is the records currently in its table, which Teneo reads. You choose **Delete template, keep the data** (the records stay and Teneo keeps working, but you can no longer Update or Reset them) or **Delete template and the data** (both go: the table itself is deleted too, unless another config uses it, then only this config's records). Neither can be undone. A table that no config points at any more (for example one you kept earlier) can be removed in the **Configs** tab, under *Tables without a config*.
- Every load writes a marker (`_meta/seed`), so the app shows which config is in a table and when it was loaded.

**From the command line:**

```bash
python seed.py                              # list configs and tables
python seed.py retail --table demoretail    # load into a table
python seed.py retail --table demoretail --reset
python seed.py --all                        # every config into its own usual table
python seed.py --export backup.json --table demoretail
```

**Dates stay fresh.** In `data.json`, write `{{today-7d}}`, `{{today+2w}}`, `{{today-1m}}` (or `{{iso:today}}` for
`2026-10-15`). They are filled in when the config is loaded, so "damage occurred 7 days ago" is always true.
For fixed days of the previous calendar month or year use `{{lastmonth:3}}` (the 3rd of last month) and `{{lastyear:03-15}}` (15 March last year), or `{{month:today-1m}}` for just the month name. That keeps "last month" and "last year" true whenever the config is loaded.

**Your own config, two ways:**

1. *Snapshot:* tweak a table in the app (rename a customer, change a status, add records), then **Configs →
   Save this table as a new config**. It appears in the picker and keeps the look of the config it came from.
2. *By hand:* copy a folder in `datasets/`, rename it, and edit three small files:
   - `config.json`: `title`, `description`, `icon`, `default_table`, `order`.
   - `data.json`: `{collection: {record id: record}}`.
   - `view.json` (optional): how the app shows it, see below.

### How the app shows data

It works on any JSON by looking at field names and values: ✉️ email (a link), 📞 phone, 🏠 address, 🎂 date of birth,
📅 dates, 💶 money (with the record's `currency`), `#️⃣` ids, status badges (green, amber, red), ✓/✗ for booleans,
chips for lists, small tables for lists of objects, and sub-sections for nested objects. Records with a `customer_id`
appear under their customer. Collections get an icon by name: 🛒 orders, 📋 claims, 📁 cases, ⚠️ issues, 🎫 tickets,
📅 appointments, 🧾 invoices, 🔁 subscriptions, 💳 cards and payments, 📝 requests, 💬 quotes, 💻 assets, and more.

A `view.json` refines this, and everything in it is optional:

```json
{
  "primary": "customers",
  "collections": {
    "customers": { "label": "Patients", "label_one": "Patient", "icon": "🧑‍⚕️", "accent": "teal",
                   "subtitle": ["plan"], "first": ["email", "phone", "date_of_birth"] },
    "orders":    { "link": "customer_id", "title": "id", "first": ["placed", "total"],
                   "labels": { "payment_method": "Paid with" }, "hide": ["internal_note"] }
  }
}
```

`link` = the field pointing at the primary record. `title` = the field used as card title. `subtitle` = fields shown as chips.
`first` = fields to show first. `hide` = fields never shown. `labels` = display names. Accents: blue, green, amber, orange,
red, purple, teal, slate.

## 3. One table per demo: setting it up in Azure

Tables are cheap and separate, so each demo gets its own: its data can be reset without touching anyone else's.
Table names are **3-63 letters and digits, starting with a letter**: no spaces, dashes or underscores (so `demoit`,
not `it` or `demo_it`).

> These steps create things in Azure. Run them yourself.

**Create the example tables** (one per config). You need the account name and a SAS token that has the
**Create** permission (see step 4b). In a terminal with the Azure CLI:

```bash
az storage table create --name demoinsurance --account-name <account> --sas-token "<SAS token>"
az storage table create --name demoretail    --account-name <account> --sas-token "<SAS token>"
az storage table create --name demotelco     --account-name <account> --sas-token "<SAS token>"
az storage table create --name demobanking   --account-name <account> --sas-token "<SAS token>"
az storage table create --name demomedical   --account-name <account> --sas-token "<SAS token>"
az storage table create --name demoit        --account-name <account> --sas-token "<SAS token>"
```

Or in the portal: your storage account → *Data storage → Tables → + Table* → type the name → OK.
Creating the table up front is optional: loading a config into a table that doesn't exist creates it.

**Fill them** (after `.env` is set up, step 5):

```bash
python seed.py --all
```

**Create the table for a new demo** (say one for Exemplario):

1. Pick a name: `demoexemplario`.
2. Create it (portal, or `az storage table create --name demoexemplario ...`), or skip this and let it be created with the data.
3. In the app: sidebar → **Add New Dataset** → name it, enter the table `demoexemplario`, start from a copy of `retail` (or paste your own JSON) → **Create config**.
   (Or from the command line: `python seed.py retail --table demoexemplario`.)
4. Connect Teneo to it (below).

**Connect a Teneo solution to a table.** The solution's engine script reads a solution secret:

| Secret (solution scope) | Value |
|---|---|
| `ext_data_account_url` | `https://<account>.table.core.windows.net` |
| `ext_data_sas_token` | the SAS token |
| `ext_data_table` | the table of this demo, e.g. `demoexemplario` (default if missing: `extdata`) |

Secret names are **case-sensitive**: use lowercase, as above. After changing secrets, restart the live test.
The same account and token can be shared by all solutions; only `ext_data_table` differs per demo.

## 4. Azure setup, once

**a. Create a storage account** (portal: *Storage accounts → Create*; Standard, locally-redundant storage is enough).
Use your resource group. With the CLI:

```bash
az storage account create --name <uniquename> --resource-group <your resource group> \
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false
```

Storage account names are 3-24 lowercase letters and digits, unique across all of Azure ("already taken" means
someone else has it). Cost at demo volumes: cents per month.

**b. Create a SAS token** (portal: your storage account → *Security + networking → Shared access signature*):

- Allowed services: **Table**
- Allowed resource types: **Service, Container, Object**
- Allowed permissions: **Read, Write, Delete, List, Add, Create, Update**
- Expiry: pick a date (shorter is safer; set a reminder to renew). Allowed protocols: **HTTPS only**
- *Generate SAS and connection string* and copy the **SAS token**.

Or with the CLI:

```bash
az storage account generate-sas --account-name <uniquename> --services t --resource-types sco \
    --permissions racwdlu --expiry 2027-06-30T00:00Z --https-only -o tsv
```

> The SAS token is a password for your data. Don't commit it, don't paste it into chats or tickets, and keep it out
> of `.env.example` (that file is the shareable template). If it leaks, rotate the account keys (*Access keys*), which
> invalidates every SAS token. For people who only need to look, create a second token with only **Read** and **List**.

**c. Note the table endpoint:** `https://<uniquename>.table.core.windows.net`
(portal: storage account → *Settings → Endpoints → Table service*).

## 5. Connect the Streamlit app

Copy `.env.example` to `.env` and fill it in:

```
EXT_DATA_ACCOUNT_URL=https://<uniquename>.table.core.windows.net
EXT_DATA_SAS_TOKEN=?sv=...
EXT_DATA_TABLE=demoretail          # only used for a config that names no table; each config otherwise brings its own
```

Then `streamlit run app.py`. In the app:

- **Sidebar:** the dataset with **Refresh database**, **Update database**, **Update seed data**, **Reset database** and **Delete template**; **Add New Dataset**; **Storage Connections** (Azure and GitHub, each with a rounded name label, the address and a test button; GitHub shows the repository link with the branch in brackets); and the dark mode switch at the very end. There is no table picker: the table comes with the config.
- **Browse:** pick a collection, search, and see a customer with all their records; or browse any other collection as cards. Links show as a short label (for a receipt: "Receipt DOC8001") instead of the whole address.
- **Tables:** every collection as a plain table.
- **Edit records:** change a record's JSON, add one, or delete one. Teneo sees it on its next read.
- **Configs:** edit a config's `data.json`, or save the current table as a new config.

**Dark mode:** at the very end of the sidebar, below **Storage Connections**, switch **🌙 Dark mode** on or off. The page reloads once and stays on the same config; the app remembers the choice in that browser. Until someone chooses, the app follows the system setting (the ⋮ menu, top right, offers the same choice). This comes from `.streamlit/config.toml`, so run `streamlit run app.py` from this folder. (To force dark with no switcher, see the comment in that file.) The table you are looking at is shown under the config in the sidebar and as the last line of the Browse tab.

The table list needs the **List** permission on the **Service** resource type. If it is missing, the app says so
and you can still type a table name.

## 6. The Teneo solution "Ext Data Demo"

| Part | What it does |
|---|---|
| Engine script **Ext Data Store (Azure Table Storage client)** | The Groovy client (`teneo/ExtDataStore.groovy`). Event: `onsolutionloadedsecret`. |
| Toolkit **Ext Data** | Tools `read_record`, `list_records`, `find_records` (look up by a field, e.g. a phone number), `load_customer_context` (a customer and everything linked to them in one request), `write_record`, `set_field`, `delete_record`. Each sets `apiCallSuccessful` and, on failure, `errorMessage`. |
| Flow **Customer record demo** | Asks for a customer ID, reads the record, greets the customer, asks for a contact preference, writes the changed record back. Say *"my account"*, then `C001`. |
| Flow **Proactive loan offer (outbound)** | For the `banking` config. Takes the global variable `calleeNumber`, loads the customer and everything linked to them in one request, and, if the customer has an open alert and is eligible, walks through the loan conversation: interest, example offer, amount and reason, duration or alternatives, confirmation. It writes the outcome back: the alert status and a new loan request. Say *"proactive loan call"* in the live test. |
| Flow **Fallback** | Points the user to the demo. |

The example flow reads `customers/<id>` and uses `name`, `plan` and `preferences.contact_by`, which every config has. So you
can run the same flow against any demo table by changing the `ext_data_table` secret.

### Using it in your own flows

Add a **Use tool** node, pick the toolkit **Ext Data** and a tool, and map inputs and outputs:

```
read_record    in:  collection  'customers'      out: customer <- record
               in:  recordId    customerId            found    <- found
                                                       apiOk    <- apiCallSuccessful
write_record   in:  collection  'customers'      out: apiOk    <- apiCallSuccessful
               in:  recordId    customerId
               in:  record      customer
```

- Inputs are script expressions, so literal text needs quotes (`'customers'`).
- `record` is a Groovy Map: `customer.name`, `customer.preferences.contact_by`, ...
- `write_record` replaces the **whole** record: read it, change the Map, write it back.
  For a single value use `set_field` (`fieldPath` may be nested, e.g. `preferences.contact_by`).
- Branch on `apiOk` first (store unreachable?), then on `found`.
- **Prefer one call to many.** Every tool call is a round trip to Azure. When a flow needs a customer and what belongs to them, use `load_customer_context` (collection `customers`, field `phone`, value the caller's number): it returns `customer`, `related` (e.g. `related.accounts`, `related.alerts`: records whose `customer_id` is the customer's) and `shared` (catalogues and settings such as `shared.loan_offers`, `shared.rules`) from a single request.
- To find a customer by phone number, or a customer's orders or claims: `find_records` (collection, field, value), e.g. `customers` / `phone` / `calleeNumber`, or `orders` / `customer_id` / `customerId`. Phone numbers are compared by their digits, so `+393470000301` matches `+39 347 000 0301`.

From any script you can also call the client directly: `ExtData.store().get("customers", "C001")`,
`.put(...)`, `.all("orders")`, `.delete(...)`.

### The outbound loan call (banking)

1. Load the banking config into its table (`python seed.py banking --table demobanking --reset`).
2. Set the solution secret `ext_data_table` to `demobanking`, and restart the live test.
3. The solution variable `calleeNumber` holds the number being called (in production the outbound call sets it; here it defaults to Elena Bianchi's). Say *"proactive loan call"*.
4. To see the cases that must **not** get an offer, set `calleeNumber` to Chiara Esposito (`+39 349 000 0303`, identity check pending) or Francesca Romano (`+39 338 000 0305`, existing loan).

The call is triggered by the customer's open alert (status `new`), the way a real detector would hand over. The conversation uses the data as it is: the amounts, instalments and APR come from the `loan_offers` collection, the threshold from the alert and the `rules` collection, and the eligibility limit from the customer's `credit_profile`. Change them in the app and the conversation changes.

### Setting up another demo solution

Duplicate **Ext Data Demo** in Teneo, add the three secrets (same account and token, a different `ext_data_table`),
and build your flows on the toolkit.

### The banking password reset (by phone)

Use case 3 of the `banking` config: a customer calls the helpline, is recognised by the phone number and told the online banking password is blocked, answers a security interview,
and the assistant either hands over to an operator (case 3A) or asks the backend for a reset and tells the customer the result (case 3B, OK or KO).

| Where | What it holds |
|---|---|
| `customers.online_banking` | `password_status` (active / blocked), `blocked_since`, `failed_attempts`, `blocked_reason`, `last_login`, a masked `username_masked` |
| `customers.identity` | `id_card_masked` (e.g. `CA***71AB`) and `tax_code_masked` (e.g. `BNC***84B**F205*`) |
| `accounts.iban` | the existing IBAN: the interview asks for its **last three digits** |
| `identity_checks` | the interview log: one row per attempt (`attempt`, `result` passed/failed, `failed_fields`, the answers, masked) |
| `password_requests` | what the backend banking service answered: `result` OK/KO, `reason_code`, `detail`, `status`, `delivery` |
| rule `R-IDENTITY` | the three questions, where the expected answer comes from, pass rule, max 3 attempts a day |
| rule `R-PWRESET` | the process, the operator hand-over (queue and note) for 3A, the backend request for 3B, and a sentence for every KO reason (`ko_reasons`) |

**Critical data is masked.** Full ID card numbers and tax codes are never stored: a `*` stands for a hidden character, so an answer is compared with the **visible** characters only
(ignoring case and spaces). The same goes for the answers logged in `identity_checks`.

| Customer | Phone | Situation |
|---|---|---|
| Marco Rossi (C002) | +39 333 000 0302 | blocked, no history: the clean case. 3A or 3B ends OK |
| Chiara Esposito (C003) | +39 349 000 0303 | blocked, three failed interviews today (the maximum) and KYC pending: no self-service, hand over to an operator. Yesterday's reset was refused (`KYC_PENDING`) |
| Alessandro Conti (C004) | +39 335 000 0304 | blocked; passed the interview this morning and already got a temporary password: a new request is refused (KO, `ALREADY_REQUESTED`) |
| Elena Bianchi (C001), Francesca Romano (C005) | | password active: nothing to reset |

The dates are placeholders (`{{today}}`, `{{today-1d}}`...), so "blocked since today" is true whenever the config is loaded.

### The banking transactions (finding documents)

One collection, `transactions`. Each row has: `reference` (CRO for SEPA transfers, TRN for the rest), `booking_date` and `value_date`, `payment_type` (SEPA transfer, CBILL, F24, card, direct debit), `beneficiary_name`, `beneficiary_iban` (SEPA transfers), `causale` (the free-text description, e.g. "Affitto settembre"), `amount` (negative = money out), `status`, and the receipt: `doc_id`, `doc_category`, `doc_title`, `doc_url`. Each customer has one `documents_page_url`, for when there are too many results to list (rule `R-DOCLINKS`: more than 5).

29 transactions for Alessandro Conti (C004), Elena Bianchi (C001) and Marco Rossi (C002), built to test how many results come back:

| Case | Where |
|---|---|
| 1 result | Alessandro: the notary (Studio Notarile Bruno); Marco: the car deposit (Autosalone Bellini) |
| 2 to 5 results | Alessandro: the electrician last month, 3 transfers, 1,200 EUR (a 4th, made today and pending, is not "last month") |
| more than 5 results | Alessandro: 7 renovation transfers last year; Elena: rent, one a month for 8 months |
| older than 6 months | 13 of the 29, e.g. last year's renovation, rent 7 and 8 months ago, Marco's tuition 9 months ago |
| messy payee names | "G. Ferrari", "Ferari Giorgio", "VITALE MARCO PIASTRELLE", "M. Vitale", "Costa Ed. Srl", "Greco P. Idraulica", "Tinteggiature Rosi", "IMMOB. DUOMO SRL"; no name at all (only in the `causale`): the window fitter "Serramenti Torino snc" and one rent payment |

The beneficiary IBAN of a payee stays the same however the name is spelled, so it can identify the payee when the name does not. Dates are relative to the day the config is loaded; reload it to keep "last month" and "last year" true.

## 7. Hosting the documents (Blob Storage + SAS link)

In the banking config every transaction carries its own receipt link (`doc_url`). Here is how to make those links real: the PDFs sit in a **private** Blob container, and each link carries a **read-only SAS token** that opens exactly that container and nothing else. You don't need to allow anonymous access.

> **Use a separate token for this.** Never put your table SAS token (the one in `.env`) into document links: it can read and write your data, and these links go to customers. The document token must be **read-only and limited to the `documents` container**.

> These steps create things in Azure. Run them yourself.

**1. Create a private container** named `documents` (portal: your storage account → *Data storage → Containers → + Container*, *Public access level: Private*), or:

```bash
az storage container create --account-name <account> --name documents --auth-mode login
```

(`--auth-mode login` needs the role *Storage Blob Data Contributor* on the account. If you don't have it, add `--account-key "<key>"` instead of it, or use the portal.)

**2. Create a read-only SAS token for the container, with a stored access policy.** The policy is what lets you extend or revoke all links later without touching anything else.

Portal: container `documents` → *Access policy* → *Add policy* (name `demo-read`, permissions **Read** only, expiry a date far enough away, e.g. next year) → *Save*. Then *Shared access tokens*: Signing method **Account key**, Stored access policy **demo-read** → *Generate SAS token and URL* → copy the **Blob SAS token** (starts with `sv=` or `si=`).

Or with the CLI:

```bash
az storage container policy create --account-name <account> --container-name documents --name demo-read --permissions r --expiry 2027-06-30T00:00Z --auth-mode login
az storage container generate-sas --account-name <account> --name documents --policy-name demo-read --https-only --account-key "<account key>" -o tsv
```

(If your subscription blocks account keys, a stored policy isn't possible. Use a user delegation token instead: `az storage container generate-sas --account-name <account> --name documents --permissions r --expiry <a date at most 7 days away> --as-user --auth-mode login --https-only -o tsv`. It expires within 7 days, so it needs renewing.)

**3. Put the container address and the token in `.env`:**

```
EXT_DATA_DOCS_URL=https://<account>.blob.core.windows.net/documents
EXT_DATA_DOCS_SAS=<the token>
```

**4. Make the sample files** (all fictional, each says so). Do this *after* step 3: the documents pages contain the links, token included.

```bash
python make_documents.py
```

This writes `documents_out/banking/`: one PDF per transaction (named after its `doc_id`, e.g. `DOC8001.pdf`) and one page per customer (`documents-C004.html`, the customer's `documents_page_url`) listing their documents with links.

**5. Upload the files** (portal: open the container → *Upload*, select everything in `documents_out/banking`), or:

```bash
az storage blob upload-batch --account-name <account> --destination documents --source documents_out/banking --overwrite --auth-mode login
```

**6. Load the config again**, so the links in the data point at your files:

```bash
python seed.py banking --table demobanking --reset
```

**7. Check it:** in the app, open a transaction, click its receipt link and open it in a private window: the PDF should show without any sign-in. Open the customer's `documents_page_url` too. Alessandro Conti (C004) is the one with the most documents.

Good to know:

- **Anyone with a link can open that container's files** until the policy expires or you delete it. That is fine for fictional documents; never put real documents here.
- **Extend or revoke:** change the expiry of the policy `demo-read`, or delete it (portal: container → *Access policy*). Every link stops or keeps working at once; no need to change any data.
- **The config's links contain the token.** If you create a new token, load the config again.
- **Another demo, another container:** use a different container name in `EXT_DATA_DOCS_URL`, and a policy per container.
- **If the documents page downloads instead of showing:** the file's content type must be `text/html`. Uploading with `upload-batch` sets it from the extension; if it still downloads, re-upload that file with `--content-type text/html`.
- In a Teams chat, send links as normal `https://` links. The long token is fine.

## 8. GitHub and hosting the app

**Why:** a hosted app (Streamlit Community Cloud) loses its local files at every restart. If **Update seed data** commits to GitHub, the seed data survives, every change has a message and a history, and the hosted app redeploys itself from it.

### Put it on GitHub

1. Create an empty **private** repository (it holds customer demo data), e.g. `database-demo-config`. No README, no .gitignore.
2. Check that `.env` is not in the list, then push:

```bash
git add -A
git status                       # .env must NOT appear
git commit -m "Ext Data template"
git remote add origin https://github.com/<owner>/<repository>.git
git push -u origin main
```

> **OneDrive:** a `.git` folder inside a OneDrive folder is slow and can get damaged by syncing. After pushing, work from a normal folder (for example `C:\dev\database-demo-config`, via `git clone`) and delete the OneDrive copy.

### A token for the app

GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*:
**Repository access:** *Only select repositories* → this one. **Permissions:** *Contents: Read and write* (nothing else).
Set an expiry and note it down: when it runs out, Update seed data says "the token is wrong or expired".

Put these in `.env` (local) or in the hosting secrets (below):

```
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=<owner>/<repository>
GITHUB_BRANCH=main
```

With them set, the sidebar shows **GitHub: ...** and **Test GitHub connection** (read-only), and **Update seed data** asks for a **commit message** (pre-filled, you can change it) and commits `datasets/<name>/data.json` to the branch. The commit comes first: if GitHub refuses it (wrong token, protected branch), nothing is changed locally. If the repository already has exactly this content, no commit is made. Without these settings the button works as before and only writes the local file (and `data.backup.json`).

### Host it on Streamlit Community Cloud

1. share.streamlit.io → *Create app* → pick the repository, branch `main`, main file `app.py`. (A private repository asks you to connect your GitHub account.) Under *Advanced settings* choose the app address, e.g. `database-demo-config-app` (it becomes `https://database-demo-config-app.streamlit.app`).
2. *Advanced settings → Secrets*: paste the settings as TOML, one per line, quoted:

```toml
EXT_DATA_ACCOUNT_URL = "https://<storageaccount>.table.core.windows.net"
EXT_DATA_SAS_TOKEN = "?sv=..."
EXT_DATA_DOCS_URL = "https://<storageaccount>.blob.core.windows.net/documents"
EXT_DATA_DOCS_SAS = "?sv=..."
GITHUB_TOKEN = "github_pat_..."
GITHUB_REPO = "<owner>/<repository>"
GITHUB_BRANCH = "main"
```

3. **Restrict who can open it** (*Share* in the app menu → only invited people). Anyone who can open the app can write to your Azure table and commit to the repository.
4. Every push to `main` (including the app's own commits) redeploys the app within a minute. Open pages reload; nothing is lost, because the data lives in Azure and the seed data in the repository.

### GitHub Actions

`.github/workflows/tests.yml` runs `check_datasets.py` and all tests on every push and pull request. A seed file that no longer loads turns the run red, also when it came from the app. It needs no secrets and never touches Azure.

### What is and is not committed

**Datasets from GitHub:** when the app starts, and on **Refresh database**, it also downloads the datasets that are in the repository but not in the app's `datasets/` folder yet (for example one made on another machine). A dataset needs `config.json` and `data.json`. Datasets that already exist are never overwritten and nothing is deleted. Without the GitHub settings this step is skipped.

The sample PDFs and documents pages are never committed: `documents_out/`, `*.pdf` and `documents-*.html` are in `.gitignore`, and the app only ever commits `datasets/<name>/data.json`. Generate them with `make_documents.py` and upload them to Blob Storage (section 7).

**Update seed data** commits `datasets/<name>/data.json`. **Delete template** removes the template's whole folder (seed data, config and every other file in it) from the repository in one commit, with a message you can edit; the commit is made first, so if GitHub refuses it nothing is deleted, and the history there lets you get the config back. **Add New Dataset** and the **Edit** tab's `data.json` editor still change local files only: on a hosted app that is lost at the next restart. Do those on your own machine, then `git commit` and `git push`.

## Troubleshooting

| Message | Likely cause |
|---|---|
| `HTTP 403 (the SAS token is wrong, expired, or lacks permission ...)` | Token expired, copied incompletely, or missing a permission (writing needs *Write/Add/Update*, creating a table needs resource type *Service* and *Create*). |
| `Unknown secret name: ext_data_account_url` (Teneo) | The secret is missing in this solution, or its name differs in case. Add it, then restart the live test. |
| Teneo says a record or customer doesn't exist, but the app shows it | Teneo is reading another table: check the `ext_data_table` secret (lowercase, exact table name). A missing table looks the same as a missing record. The engine also keeps its connection from when it loaded: after changing a secret, **restart the live test** (or reload the solution). |
| `Cannot reach Azure Table Storage` | Wrong account URL, or a storage firewall that blocks the caller (Networking tab of the storage account). |
| `The table '...' was deleted a moment ago` | Azure removes a deleted table in the background: the same name can't be created again for about 40 seconds. Try again shortly. |
| After deleting a template, the empty folder `datasets/<name>` remains | Windows refuses to remove a folder another program has open: usually a File Explorer window showing it, a terminal whose current folder is inside it, or OneDrive syncing. The config itself is gone (the files are deleted); close the window or terminal and the app removes the empty folder at its next start, or delete it by hand. |
| `Table name '...' is not valid` | Use 3-63 letters and digits only, starting with a letter. |
| `Record ... is N characters; the limit is 32000` | Keep records smaller, or split into several records. |
| `GitHub said HTTP 403/404 ...` | The token lacks *Contents: Read and write* on this repository, is for another repository, or the branch is protected. **Test GitHub connection** in the sidebar tells which. |
| Teneo warns about a `LazyMap` variable | An old copy of the engine script. The current one parses JSON with `JsonSlurperClassic`; update the engine script and restart. |
| `No signature of method: java.util.Date.format` | An old copy of the engine script (it used a Groovy helper that Teneo lacks). Update it. |

## Tests

```bash
python test_datasets.py      # configs, placeholders, loading and resetting, rendering
python test_documents.py     # sample PDFs, documents pages, document links
python test_github.py        # commits to GitHub (against a fake GitHub on localhost), Update seed data
python test_store.py         # storage layer
python check_datasets.py     # every config loads and validates
```

`test_store.py` always tests the local backend. For the Azure backend, start the Azurite emulator first
(`npx azurite-table --silent --location ./azurite-data --skipApiVersionCheck`); without it that test is skipped.
Nothing in the tests talks to real Azure.
