# Saidkick Documentation

Saidkick is a lightweight, remote browser inspection and automation tool designed to bridge the gap between your terminal and your web browser. It provides real-time log mirroring, remote DOM inspection, and arbitrary JavaScript execution in the context of any open web page.

## Core Functionalities

- **Console Mirroring**: Stream browser console logs (`log`, `warn`, `error`, `info`) directly to your terminal or a central server.
- **Remote DOM Inspection**: Retrieve the HTML content of any page using CSS selectors or XPath.
- **Remote Interaction**: Click elements, type text, and select options remotely via CLI or API.
- **JS Execution**: Run arbitrary JavaScript on a page and receive the return value.
- **CSP Bypass**: Uses the Chrome Debugger API to execute scripts even on pages with strict Content Security Policies.

## Project Structure

Saidkick consists of three main parts:

1.  **FastAPI Server**: The central hub that manages browser connections and exposes a REST API for automation.
2.  **Chrome Extension**: A background worker and content script that executes commands and forwards logs.
3.  **CLI & Client**: A Typer-based command-line tool and a reusable Python client library for interacting with the server.

## Getting Started

- To set up and run Saidkick, see the [Deployment Guide](deploy.md).
- To learn how to use the CLI and Python client, see the [User Guide](user-guide.md).
- For a deep dive into the architecture, see the [Design Document](design.md).
- If you want to contribute, check the [Development Guide](develop.md).
