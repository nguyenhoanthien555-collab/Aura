---
name: project-structure
description: Overview of AURA project structure and current progress
metadata:
  type: project
---

# AURA Project Structure

## Root Level
- `android/` - Android native app (Kotlin)
- `apps/` - Web apps (Next.js)
- `packages/` - Shared packages
- `references/` - Documentation and design references
- `.github/` - GitHub workflows and templates
- `.claude/` - Claude Code configuration

## Key Directories

### android/
- Main Android app with accessibility features
- Kotlin-based companion app
- Accessibility service for gesture actions

### apps/
- `web/` - Web application (Next.js)
- `mobile/` - Mobile web wrapper

### packages/
- Shared code between platforms
- Common utilities and services

## Recent Work
- Added Android accessibility agent with gesture support
- Integrated Groq and Mistral cloud providers with failover
- Implemented personality consistency across providers
