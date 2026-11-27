# Reading an unreadable image

Load this workflow when text in a user-provided image is unreadable or unclear. Use authorized image inputs and the distinct methods below as available.

1. Inspect the original image directly at its available resolution.
2. Inspect the relevant region after cropping, enlarging, or applying an appropriate readability enhancement. Preserve the original and verify any inferred text against it.
3. Extract text using an available OCR engine, then compare its output with the image. Treat OCR output as uncertain until checked.

Count an earlier attempt toward its matching method and queue the remaining methods for text that is still unclear. If a method outside these three failed, assess all three and carry out each available, authorized method. For each attempt, record the input view or region and which portions were confirmed or remain unresolved. Mark a method that is unavailable or unauthorized as NOT_RUN and state the limitation.

When the text is readable, answer the user's request with what you verified against the image and identify any remaining uncertainty. Request transcription only after all three methods were attempted and failed.
