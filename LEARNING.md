

## Confirmed image learning (controller 1.1.3, October 6, 2026)

Saving an identity correction confirms its linked subject image. The background worker builds Type references even for Unknown subjects; named Person/Cat/Dog crops also feed the existing individual gallery when image quality permits (people require a usable face). Whole-clip labels without an image cannot supply visual training evidence. Existing confirmed images are picked up automatically in batches of four, with Live View pauses. Repeated saves replace the same reference; later edits and removals invalidate it. Unusable images are marked processed rather than retried forever.

Vehicle suggestions now require at least 0.85 detector score, a full-scene detection (pet detail tiles cannot propose vehicles), and a matching track in at least two sampled frames. A bounded gallery of at most 128 recent examples per Type adds a conservative veto: two close confirmed nonvehicle images of the same Type, from distinct clips and with different image hashes, must both have cosine similarity at least 0.92 and exceed the best confirmed Vehicle reference by at least 0.05. The current clip is excluded. Similarity is not calibrated confidence. This veto suppresses the Vehicle suggestion; it does not fabricate another Type or identity. Base model weights are unchanged. Older labels and human confirmations are not automatically rewritten. Vehicle triggers and camera configuration are unchanged.

Release validation: 49 isolated Pi regression checks covering identity/crop flows, vehicle migration, Type references, stale edits, repeated saves, unusable images, confidence/full-scene filtering and repeated-frame requirements.
