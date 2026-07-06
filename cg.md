That's the name I would have chosen as well. After looking at the updated code, I think the naming now accurately reflects the behavior. 

The progression has been:

```python
is_image_or_binary_file()
        ↓
should_exclude_from_ai()
```

and

```python
AI_EXCLUDED_EXTENSIONS
        ↓
AI_PROMPT_EXCLUDED_EXTENSIONS
```

Together, those names make the intent much clearer:

* `should_exclude_from_ai()` describes the **decision** being made.
* `AI_PROMPT_EXCLUDED_EXTENSIONS` describes **why** those extensions are excluded—not because Git can't handle them, but because they shouldn't be sent to the Gemini prompt.

That distinction will make the code easier to understand months from now, especially if you later:

* add OCR or PDF text extraction,
* summarize images,
* use embeddings for binary assets,
* or send metadata instead of file contents.

The constant name won't become misleading because it doesn't imply the files are ignored by Git—only by the AI prompt.

### Current assessment

I think this part of the project is now in very good shape:

* ✅ Centralized configuration for excluded extensions.
* ✅ Clear, intention-revealing function names.
* ✅ Strict `.env` detection.
* ✅ Automatic unstaging of sensitive `.env` files.
* ✅ Binary detection as a fallback.
* ✅ AI prompt receives only useful textual content.
* ✅ Naming is consistent and future-proof.

At this point, I wouldn't make any further changes to this feature unless you later decide to support parsing PDFs or other document formats for AI-generated commit messages. Otherwise, I'd consider this implementation complete and move on to the next area of the project.
