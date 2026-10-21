//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
} from "@mui/material";

import { ApiError } from "@/api/client";
import { checkItem, createItem, forkItem, useKinds } from "@/api/items";
import { pickEditScope, useScopes } from "@/api/scopes";
import { ScopeSelect } from "@/components/ScopeSelect";

type Props = {
  open: boolean;
  kind: string;
  scopeId: string;
  parent?: string;
  copyFrom?: { scopeId: string; itemId: string };
  onClose: () => void;
  onCreated: (scopeId: string, id: string) => void;
};

export function CreateItemDialog({
  open,
  kind,
  scopeId,
  parent,
  copyFrom,
  onClose,
  onCreated,
}: Props) {
  const { data: kinds = [] } = useKinds();
  const { data: scopes = [] } = useScopes();
  const info = kinds.find((k) => k.id === kind);

  const [target, setTarget] = useState(scopeId);
  const [name, setName] = useState("");
  const [version, setVersion] = useState(info?.versioned ? "v1" : "");
  const [template, setTemplate] = useState("blank");
  const [check, setCheck] = useState<{ ok: boolean; reason?: string; copy_into?: string[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTarget(pickEditScope(scopes, scopeId, kind));
    setVersion(info?.versioned ? "v1" : "");
  }, [scopeId, info, open, scopes, kind]);

  useEffect(() => {
    if (!open || !name.trim()) {
      setCheck(null);
      return;
    }
    const handle = window.setTimeout(() => {
      void checkItem(target, { kind, name: name.trim(), version: version || undefined }).then(setCheck);
    }, 250);
    return () => window.clearTimeout(handle);
  }, [open, target, kind, name, version]);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      if (copyFrom) {
        const created = await forkItem(copyFrom.scopeId, kind, copyFrom.itemId, {
          name: name.trim(),
          version: version || undefined,
          target_scope: target,
        });
        onCreated(target, created.id);
      } else {
        const created = await createItem(target, {
          kind,
          name: name.trim(),
          version: version || undefined,
          template: template === "blank" ? undefined : template,
          parent,
        });
        onCreated(target, created.id);
      }
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        const detail = err.detail as { copy_into?: string[] };
        setCheck({ ok: false, reason: "read_only", copy_into: detail.copy_into });
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>New {info?.id ?? kind}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <ScopeSelect
            label="Scope"
            scopes={scopes}
            value={target}
            onChange={setTarget}
            writableOnly
            kind={kind}
          />
          <TextField
            label="Name"
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            helperText={check && !check.ok ? check.reason : info?.name_pattern}
          />
          {info?.versioned && (
            <TextField
              label="Version"
              size="small"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
          )}
          {(info?.template_ids?.length ?? 0) > 0 && (
            <TextField
              select
              label="Template"
              size="small"
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
            >
              {(info?.template_ids ?? ["blank"]).map((id) => (
                <MenuItem key={id} value={id}>
                  {id}
                </MenuItem>
              ))}
            </TextField>
          )}
          {check?.reason === "read_only" && (check.copy_into?.length ?? 0) > 0 && (
            <Alert severity="info">
              This scope is read-only. Copy into a writable scope:
              <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: "wrap" }}>
                {check.copy_into!.map((id) => (
                  <Button key={id} size="small" onClick={() => setTarget(id)}>
                    {id}
                  </Button>
                ))}
              </Stack>
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          disabled={busy || !name.trim() || check?.ok === false}
          onClick={() => void submit()}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}
