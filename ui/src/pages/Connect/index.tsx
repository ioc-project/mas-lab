//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useNavigate } from "react-router";
import { Alert, Box, Button, Stack, TextField, Typography } from "@mui/material";

import { getApiBaseUrl } from "@/api/client";
import {
  clearStoredConnection,
  getStoredConnection,
  setStoredConnection,
} from "@/api/connection";
import { fetchApiInfo } from "@/api/info";

export default function Connect() {
  const navigate = useNavigate();
  const stored = getStoredConnection();
  const [address, setAddress] = useState(getApiBaseUrl());
  const [token, setToken] = useState(stored?.token ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const test = async () => {
    setBusy(true);
    setError("");
    const previous = getStoredConnection();
    const next = { address: address.replace(/\/$/, ""), token };
    setStoredConnection(next);
    try {
      await fetchApiInfo();
      navigate("/", { replace: true });
    } catch (err) {
      if (previous) setStoredConnection(previous);
      else clearStoredConnection();
      setError(err instanceof Error ? err.message : "Cannot reach the controller");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ maxWidth: 480, margin: "4rem auto", padding: 2 }}>
      <Typography variant="h5" sx={{ mb: 2 }}>
        Connect to MAS Lab
      </Typography>
      <Typography variant="body2" sx={{ mb: 3, color: "text.secondary" }}>
        The UI talks to the controller over HTTP only. Enter the API address and an optional bearer token.
      </Typography>
      <Stack spacing={2}>
        {error && <Alert severity="error">{error}</Alert>}
        <TextField
          label="Controller address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="http://localhost:8090"
          size="small"
        />
        <TextField
          label="Bearer token (optional)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          type="password"
          size="small"
        />
        <Button variant="primary" onClick={() => void test()} disabled={busy || !address.trim()}>
          {busy ? "Testing…" : "Connect"}
        </Button>
      </Stack>
    </Box>
  );
}
