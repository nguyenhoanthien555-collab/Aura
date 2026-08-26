package com.aura.companion.ui.chat

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight

fun parseMarkdownToAnnotatedString(
    text: String,
    codeColor: Color,
): AnnotatedString {
    return buildAnnotatedString {
        var currentIndex = 0
        // Match **, *, or `
        val regex = Regex("(\\*\\*.*?\\*\\*|\\*.*?\\*|`.*?`)")
        val matches = regex.findAll(text)
        
        for (match in matches) {
            val startIndex = match.range.first
            val endIndex = match.range.last + 1
            val value = match.value
            
            if (startIndex > currentIndex) {
                append(text.substring(currentIndex, startIndex))
            }
            
            when {
                value.startsWith("**") && value.endsWith("**") -> {
                    pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                    append(value.substring(2, value.length - 2))
                    pop()
                }
                value.startsWith("*") && value.endsWith("*") && value.length > 2 -> {
                    pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                    append(value.substring(1, value.length - 1))
                    pop()
                }
                value.startsWith("`") && value.endsWith("`") && value.length > 2 -> {
                    pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = codeColor))
                    append(value.substring(1, value.length - 1))
                    pop()
                }
                else -> append(value)
            }
            currentIndex = endIndex
        }
        
        if (currentIndex < text.length) {
            append(text.substring(currentIndex))
        }
    }
}
