# Scribo evaluation: findings and recommendations

## What Scribo is
Scribo is an invoice-generation service that Claude can use through four tools: create an invoice, retrieve one, list supported jurisdictions, and verify the sender's email. It supports senders in **Germany** (ZUGFeRD and XRechnung e-invoice formats, validated against the EU standard EN 16931) and the **United States** (plain PDF).

## How it was tested
- **Run:** Jurisdiction listing, email verification, one successful German invoice, an identical repeat of that request, retrieval by ID, and three deliberately invalid requests (a German XRechnung draft with several problems, and two US drafts with bad routing numbers).
- **Not run:** A successful US invoice, and the full B2G path (Leitweg-ID-based XRechnung submission).
- **Test data:** All names and addresses were fake, and the German VAT ID (`DE123456788`) was a placeholder. The sender and recipient email were the same address, belonging to the tester.

## Results

### Worked as expected
- A valid German invoice (€91,000, 19% VAT, a 1.62% line discount, Net 60 terms) passed validation and produced a ZUGFeRD COMFORT file with a download link.
- An identical request returned the same invoice ID, with no duplicate.
- Retrieval by ID returned matching metadata.

### Rejected correctly
- An XRechnung draft with five problems returned all five in one response, each tied to a field path and a rule ID (BR-E-10, BR-DE-1, BR-DE-5, BR-DE-6, and a missing Leitweg-ID).
- A US routing number with 8 digits failed the format check.
- A 9-digit number with a bad checksum failed the ABA check separately.

## Three strengths
1. **Real rules, clearly reported.** The service enforces actual tax and banking rules, such as the e-invoice standard and the ABA checksum. Each error names the exact field and rule, so it is quick to fix.
2. **Safe retries.** Identical input returns the same invoice, so network retries or repeated calls don't create duplicates.
3. **No guessing.** It won't invent invoice numbers, placeholder emails or VAT categories, and it treats the sender email as a credential that must be verified.

## Three problems
1. **It emails the recipient automatically, with no confirmation.** With a real customer's address, one mistaken call would send them an invoice. The repeat request also reported "emailed to the recipient" again, and it is unclear whether a second email went out.
2. **Verification comes before validation.** The sender receives a code by email and must enter it before learning the draft is invalid. Validation should run first.
3. **Documentation and messages are inconsistent.**
   - The tool description says German invoices default to XRechnung (UBL), but the actual default is ZUGFeRD COMFORT.
   - The verification email says "6-digit code", while the verification tool describes 6 characters from the set {2-9}.
   - A request missing the sender email returned a "verification required" error pointing at the verification tool with a null challenge ID, when the real fix was to supply an email.
   - The success response doesn't show the invoice number.

## Three general recommendations
1. **Add a dry-run mode.** It would validate everything and return a preview, with no email sent and nothing stored.
2. **Add a send control.** An option such as `send_to_recipient: false`, or a draft-then-send two-step, would make emailing the customer a deliberate action.
3. **Add early-payment terms, and list/search.**
   - A structured early-payment discount field, plus an external reference, would let negotiated terms flow onto the invoice.
   - A list and search tool is also needed, since retrieval currently requires the exact UUID.

## Three recommendations for launching in Spain
*Spain's rules are still moving. Confirm current deadlines with a Spanish tax adviser before committing a launch date.*

### 1. Build for Spain's B2B e-invoicing mandate, not just a PDF
Royal Decree 238/2026, approved on 24 March 2026, makes B2B e-invoicing mandatory for businesses and professionals in Spain. Spain will use a five-corner model that requires a structured, machine-readable file instead of PDF, Excel or paper. Two kinds of platform are expected: a free public application run by the tax agency (AEAT), which also acts as the central repository, and privately operated certified platforms.

- Scribo would need a Spain sender jurisdiction with a compliant structured format, and a route to submit invoices through the AEAT platform or a certified platform. Today it only generates the file and leaves submission to the user.
- Deadlines depend on a ministerial order. The mandate phases in with one year for companies above €8 million in revenue and two years for everyone else. A draft of the order was published on 16 April 2026, with a deadline of 1 October 2028 for all other businesses. Whether the order has since been finalised was not confirmed.
- Scope is narrower than "any invoice in Spain". It covers domestic B2B between parties established in Spain, with B2C and cross-border transactions currently out of scope. Scribo should detect which invoices are in scope.

### 2. Treat Verifactu as a product requirement for Scribo itself
Verifactu is separate from the B2B mandate. It targets tax fraud and puts obligations on invoicing software providers, with its effects deferred until 2027. Scribo would count as invoicing software.

- Plan for software-integrity requirements, such as tamper-evident invoice records, from the deferred 2027 date. The regulation also likely requires a verification QR code and reporting to the tax agency, but check the exact technical requirements in the regulation itself.
- Some customers are exempt. Taxpayers already on the SII system are exempt from Verifactu, but they must still comply with the B2B e-invoicing obligation. The product should ask which regime the sender is under and behave accordingly.

### 3. Localise for Spanish tax, language and payment practice
- **Tax and identity:** Add Spanish IVA categories and exemptions, NIF/CIF validation, and Spanish IBAN handling, with the same depth as the German and US validators.
- **Language:** Add a Spanish (es-ES) locale for invoices and emails.
- **Public sector:** Businesses invoicing the public sector in Spain use FACe. Support for it is the equivalent of Scribo's German B2G gap.
- **Payment terms:** Spain's average payment period is about 80 days, above the 60-day maximum in its Late Payment Law. Scribo could warn when terms exceed 60 days. This also connects to early-payment discounts, since Spanish suppliers have a strong reason to accelerate cash.

## Sources for the Spain section
- KPMG, "Spain: Council of Ministers approves e-invoicing mandate for B2B transactions" (26 March 2026): https://kpmg.com/us/en/taxnewsflash/news/2026/03/spain-e-invoicing-mandate-b2b.html
- Comarch, "Spain approves Royal Decree for mandatory B2B e-invoicing": https://www.comarch.com/trade-and-services/data-management/legal-regulation-changes/spain-approves-royal-decree-for-mandatory-b2b-e-invoicing/
- fiskaly, "E-invoicing in Spain: when is it mandatory and who must comply?": https://www.fiskaly.com/blog/e-invoicing-when-is-it-mandatory-spain
- e-invoice.app, "Spain Approves Mandatory B2B E-Invoicing": https://www.e-invoice.app/blog/spain-mandatory-b2b-einvoicing
- Vertex, "Spain's 2026 E-Invoicing Regulations Explained": https://www.vertexinc.com/en-gb/node/8232