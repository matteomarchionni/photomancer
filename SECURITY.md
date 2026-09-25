# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately with
GitHub's **Report a vulnerability** button (Security tab of this repository). You will get
an answer as soon as possible; please allow time for a fix before disclosing it.

## Scope

PhotoMancer runs entirely on your computer. Its only network traffic is to LM Studio on
`127.0.0.1`; it reads your photo archive, writes `.jpg.xmp` sidecars next to the photos
and a catalogue into its data folder, and copies a backup to the folder you choose.
Relevant reports include, for example: a crafted image or EXIF block that makes
PhotoMancer write outside those places or run code, or a way it could overwrite or delete
files that are not its own sidecars.

Only the latest release is supported.
