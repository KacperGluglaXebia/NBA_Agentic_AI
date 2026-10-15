# HoopsPass Customer Data and Privacy Policy (internal)

Document HP-POL-08 · Version 4.0 · Effective 1 July 2025 · Owner: Data Protection Officer

This policy applies to every employee, contractor and AI assistant that uses HoopsPass customer data.

## 1. Personal data

1.1 Personal data (PII) in HoopsPass systems: first and last name, e-mail, phone number, date of birth, home ZIP code and the last four digits of the payment card.

1.2 PII may be shown only to staff in the PII readers group (hoopspass_pii_readers) and only for customer service tasks. Everyone else sees masked values.

1.3 Analytics, dashboards and AI assistants identify customers by customer_id only. Lists of names, e-mails or phone numbers must never be produced for analysis, marketing or "top customer" reports.

## 2. Regional access

2.1 Regional sales managers see ticket and game data for their own sales region only. Members of hoopspass_all_regions see all regions.

2.2 The restriction is enforced in Unity Catalog with a row filter and column masks, so it applies to SQL, dashboards, Genie agents and any other assistant, whoever built it.

## 3. Counting people

3.1 About 2% of customer records are duplicates from a legacy CRM import. Count people with distinct lower-case e-mail, not with customer rows.

3.2 Date of birth 1900-01-01 means "unknown" (legacy import). Exclude it from any age calculation.

## 4. AI assistants

4.1 AI assistants get read-only access, through tools that return aggregated or customer_id-level data. They never get write access to customer or order data.

4.2 An assistant asked for PII must refuse and offer an aggregated or customer_id-based answer instead.

4.3 Requests to delete, change or export customer data are handled by Customer Operations through the privacy request form, never by an assistant.

## 5. Retention

5.1 Order and ticket data are kept for 7 years for tax purposes. Marketing data of customers without opt-in is deleted after 24 months of inactivity.
