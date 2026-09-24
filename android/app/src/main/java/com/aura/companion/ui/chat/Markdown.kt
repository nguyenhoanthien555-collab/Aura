package com.aura.companion.ui.chat

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.sp

// Inline spans: **bold**, *italic*, `code`, and [label](url). Compiled once at
// class-load, not rebuilt on every call. Bold is listed before italic so `**x**`
// is matched whole rather than as two `*` runs.
private val INLINE_REGEX = Regex(
    "(\\*\\*.+?\\*\\*|\\*[^*]+?\\*|`[^`]+?`|\\[[^\\]]+?\\]\\([^)]+?\\))"
)

/** Append one line, resolving inline **bold** / *italic* / `code` / [links]. */
private fun AnnotatedString.Builder.appendInline(
    text: String,
    codeColor: Color,
    linkColor: Color,
) {
    var i = 0
    for (m in INLINE_REGEX.findAll(text)) {
        if (m.range.first > i) append(text.substring(i, m.range.first))
        val v = m.value
        when {
            v.startsWith("**") && v.endsWith("**") -> {
                pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                append(v.substring(2, v.length - 2))
                pop()
            }
            v.startsWith("`") && v.endsWith("`") -> {
                pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeColor))
                append(v.substring(1, v.length - 1))
                pop()
            }
            v.startsWith("[") && v.contains("](") -> {
                val label = v.substring(1, v.indexOf("]("))
                pushStyle(SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline))
                append(label)
                pop()
            }
            v.startsWith("*") && v.endsWith("*") -> {
                pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                append(v.substring(1, v.length - 1))
                pop()
            }
            else -> append(v)
        }
        i = m.range.last + 1
    }
    if (i < text.length) append(text.substring(i))
}

/**
 * Render a companion reply's lightweight Markdown to an AnnotatedString.
 *
 * Block level, line by line: headings, bullet lists (dash or star), numbered
 * lists (kept verbatim), block quotes, and fenced code blocks. Inside
 * each line, inline bold/italic/code/links are resolved. Deliberately small and
 * allocation-light - no external Markdown dependency - so it stays cheap enough
 * to run per visible bubble on a mid-range phone.
 */
fun parseMarkdownToAnnotatedString(
    text: String,
    codeColor: Color,
    linkColor: Color = codeColor,
): AnnotatedString = buildAnnotatedString {
    val lines = text.split("\n")
    var inFence = false
    lines.forEachIndexed { index, line ->
        if (index > 0) append("\n")
        val trimmed = line.trimStart()
        when {
            trimmed.startsWith("```") -> {
                // Toggle the fenced code block; the fence marker itself renders
                // as an empty line rather than as literal backticks.
                inFence = !inFence
            }
            inFence -> {
                pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeColor))
                append(line)
                pop()
            }
            trimmed.startsWith("### ") -> {
                pushStyle(SpanStyle(fontWeight = FontWeight.Bold, fontSize = 16.sp))
                appendInline(trimmed.removePrefix("### "), codeColor, linkColor)
                pop()
            }
            trimmed.startsWith("## ") -> {
                pushStyle(SpanStyle(fontWeight = FontWeight.Bold, fontSize = 18.sp))
                appendInline(trimmed.removePrefix("## "), codeColor, linkColor)
                pop()
            }
            trimmed.startsWith("# ") -> {
                pushStyle(SpanStyle(fontWeight = FontWeight.Bold, fontSize = 20.sp))
                appendInline(trimmed.removePrefix("# "), codeColor, linkColor)
                pop()
            }
            trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                append("•  ")
                appendInline(trimmed.substring(2), codeColor, linkColor)
            }
            trimmed.startsWith("> ") -> {
                pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                append("  ")
                appendInline(trimmed.removePrefix("> "), codeColor, linkColor)
                pop()
            }
            else -> appendInline(line, codeColor, linkColor)
        }
    }
}
