# Outcome sigils

One static image per match outcome. `manifest.json` maps `CREW_VICTORY` / `IMPOSTOR_VICTORY` / `UNRESOLVED`
to a title and an image in this folder. `chain/passport.py::select_sigil()` picks the entry at match end and
embeds it in the Match Passport (and in the NFT metadata).

To change an image, replace the SVG or point `image` at a new file.

**NFT image:** the Match Passport NFT inlines the image into its on-chain metadata only if the file is under 8 KB
(the SVGs here are). For a larger image, host it (IPFS, or a raw GitHub URL) and add `"image_url": "https://..."`
to the manifest entry; the NFT will point at that instead.
