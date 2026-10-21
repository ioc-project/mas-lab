//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { loadManifestSchemas } from "@/lib/loadManifestSchemas";
function render() {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

async function bootstrap() {
  try {
    await loadManifestSchemas();
    render();
  } catch {
    render();
  }
}

void bootstrap();
