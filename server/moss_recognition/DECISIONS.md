# Decisions

1. **Separate hosting account:** Recognizer is an independent WSGI Python service; do not embed in the sorter GUI or the customer's existing websites.
2. **No machine commands from the web:** Server results are advisory-only. Only the existing local controller may talk to the Arduino.
3. **Fail closed:** No unverified image may become a learning example. A human must approve each exact printing; ambiguous items remain pending/rejected.
4. **Low resource first:** Only Pillow and Python standard library are required; SQLite and bounded dHash gallery are appropriate as a pilot on shared hosting. Don't claim this is a neural AI model.
5. **Keep historical sorter functions:** Initial release adds *only new server files*; movement, tray routing and scanner code remain unchanged.
6. **Private, authenticated data:** Keep SQLite and scan images outside public_html. Use unique API, admin and session secrets; HTTPS required.
7. **Reproducible diagnostics:** Tests and four continuity documents travel with each release; physical sorting acceptance remains a separate step.
8. **Authoritative printing identity is future integration:** Candidate choices supplied by the client need operator verification. The catalog will be added before server-side decisions can affect live sorting.
