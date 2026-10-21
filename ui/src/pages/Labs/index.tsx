//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import {
  Alert,
  Box,
  Button as MuiButton,
  Card,
  CardActionArea,
  CardContent,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { Button } from "@open-ui-kit/core";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { scopeHref } from "@/api/scope";
import { createScope, groupScopes, useScopes, type Scope } from "@/api/scopes";
import { PageWithTitle } from "@/components";
import { CreateItemDialog } from "@/components/CreateItemDialog";

export default function Labs() {
  const theme = useTheme();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: scopes = [], isLoading } = useScopes();
  const { labs, workspace, local, packaged } = groupScopes(scopes);
  const [createLabOpen, setCreateLabOpen] = useState(false);
  const [experimentLab, setExperimentLab] = useState<string | null>(null);

  return (
    <Box>
      <PageWithTitle
        showBreadcrumbs={false}
        title={
          <Stack direction="row" sx={{ gap: "8px", justifyContent: "space-between" }}>
            <Typography
              variant="h5"
              sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
            >
              Labs
            </Typography>
            <Button onClick={() => setCreateLabOpen(true)}>New Lab</Button>
          </Stack>
        }
        subTitle="Every lab the controller can discover. Create a lab or open one to add experiments."
      >
        {isLoading && (
          <Typography variant="body2" color="text.secondary">
            Loading labs…
          </Typography>
        )}
        {!isLoading && labs.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            No labs yet. Create one, or add a path under{" "}
            <code>mas_lab.labs_search_paths</code> in config.yaml.
          </Typography>
        )}
        <Stack direction="row" sx={{ gap: "16px", flexWrap: "wrap" }}>
          {labs.map((lab) => (
            <LabCard
              key={lab.id}
              lab={lab}
              libraries={scopes}
              onOpen={() => navigate(scopeHref(lab.id, "/experiments"))}
              onNewExperiment={() => setExperimentLab(lab.id)}
            />
          ))}
        </Stack>
        <OtherLibraries workspace={workspace} local={local} packaged={packaged} />
      </PageWithTitle>
      <CreateLabDialog
        open={createLabOpen}
        onClose={() => setCreateLabOpen(false)}
        onCreated={async (scope) => {
          await queryClient.invalidateQueries({ queryKey: ["scopes"] });
          setCreateLabOpen(false);
          navigate(scopeHref(scope.id, "/experiments"));
        }}
      />
      {experimentLab && (
        <CreateItemDialog
          open
          kind="experiment"
          scopeId={experimentLab}
          onClose={() => setExperimentLab(null)}
          onCreated={(sid, id) => {
            setExperimentLab(null);
            navigate(scopeHref(sid, `/experiments/${encodeURIComponent(id)}`));
          }}
        />
      )}
    </Box>
  );
}

function LabCard({
  lab,
  libraries,
  onOpen,
  onNewExperiment,
}: {
  lab: Scope;
  libraries: Scope[];
  onOpen: () => void;
  onNewExperiment: () => void;
}) {
  const nested = (lab.libraries ?? [])
    .map((id) => libraries.find((scope) => scope.id === id))
    .filter((scope): scope is Scope => !!scope);
  const experiments = lab.counts?.experiment ?? 0;
  return (
    <Card sx={{ width: 320 }}>
      <CardActionArea onClick={onOpen}>
        <CardContent>
          <Typography variant="h6">{lab.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, minHeight: 40 }}>
            {lab.description || "No description"}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {experiments} experiment{experiments === 1 ? "" : "s"}
            {nested.length ? ` · local library ${nested.map((s) => s.title).join(", ")}` : ""}
          </Typography>
        </CardContent>
      </CardActionArea>
      <Stack direction="row" sx={{ px: 2, pb: 1.5, gap: 1 }}>
        <MuiButton size="small" onClick={onOpen}>
          Open
        </MuiButton>
        <MuiButton size="small" onClick={onNewExperiment}>
          New experiment
        </MuiButton>
      </Stack>
    </Card>
  );
}

function OtherLibraries({
  workspace,
  local,
  packaged,
}: {
  workspace: Scope[];
  local: Scope[];
  packaged: Scope[];
}) {
  const navigate = useNavigate();
  const groups: { title: string; items: Scope[] }[] = [
    { title: "Workspace library", items: workspace },
    { title: "Local libraries", items: local },
    { title: "Packaged libraries", items: packaged },
  ].filter((group) => group.items.length > 0);
  if (groups.length === 0) return null;
  return (
    <Stack sx={{ mt: 4, gap: 2 }}>
      <Typography variant="h6">Other libraries</Typography>
      {groups.map((group) => (
        <Stack key={group.title} sx={{ gap: 0.5 }}>
          <Typography variant="subtitle2">{group.title}</Typography>
          {group.items.map((scope) => (
            <Typography
              key={scope.id}
              variant="body2"
              sx={{ cursor: "pointer", textDecoration: "underline" }}
              onClick={() => navigate(scopeHref(scope.id, "/applications"))}
            >
              {scope.title}
              {scope.writable ? "" : " (read-only)"}
            </Typography>
          ))}
        </Stack>
      ))}
    </Stack>
  );
}

function CreateLabDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (scope: Scope) => void | Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [localLibrary, setLocalLibrary] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await createScope({
        kind: "lab",
        name: name.trim(),
        description: description.trim(),
        local_library: localLibrary,
      });
      await onCreated(created);
      setName("");
      setDescription("");
      setLocalLibrary(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>New lab</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="Name"
            size="small"
            value={name}
            onChange={(event) => setName(event.target.value)}
            helperText="Folder name becomes {name}.lab on the first labs_search_paths entry"
          />
          <TextField
            label="Description"
            size="small"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            multiline
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={localLibrary}
                onChange={(event) => setLocalLibrary(event.target.checked)}
              />
            }
            label="Create a local library (applications, agents, tools)"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <MuiButton onClick={onClose}>Cancel</MuiButton>
        <Button variant="primary" disabled={busy || !name.trim()} onClick={() => void submit()}>
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}
