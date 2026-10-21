//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  CodeBlock,
  TabPanel,
  CanvasBuilder,
} from "@/components";
import type { YamlOutputMap } from "@/components/CanvasBuilder/types";
import {
  Box,
  CircularProgress,
  Stack,
  Typography,
  useTheme,
  MenuItem,
  Select,
  Alert,
} from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router";
import { toTitleCase } from "@/utils/string";
import { useCallback, useEffect, useMemo, useState } from "react";
import { parse, stringify } from "yaml";
import { validateManifests } from "@/utils/manifestValidator";
import {
  useValidateAgent,
  useValidateMas,
  useMasResourceDetail,
  updateMasResource,
  runMas,
  pollJob,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { scopeHref, useScopeId } from "@/api/scope";
import { forkItem, useItem } from "@/api/items";
import { CreateItemDialog } from "@/components/CreateItemDialog";
import {
  Button,
  Dialog,
  InputField,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tab,
  Tabs,
} from "@open-ui-kit/core";

const APPLICATION_TAB_KEYS = ["graph", "yaml"] as const;
type ApplicationTab = (typeof APPLICATION_TAB_KEYS)[number];

const Application = () => {
  const theme = useTheme();
  const library = useScopeId();
  const { id, applicationTab } = useParams();
  const queryClient = useQueryClient();

  const [yamlOutputMap, setYamlOutputMap] = useState<YamlOutputMap>({});
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(
    null,
  );
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [masName, setMasName] = useState("");
  const [masDescription, setMasDescription] = useState("");
  const [masIntent, setMasIntent] = useState("");
  const [saveError, setSaveError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [runMasError, setRunMasError] = useState<string | null>(null);
  const [runMasResult, setRunMasResult] = useState<{
    message: string;
    severity: "success" | "error";
  } | null>(null);
  const [isRunningMas, setIsRunningMas] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);

  const validateMutation = useValidateAgent();
  const validateMasMutation = useValidateMas();

  const navigate = useNavigate();
  const location = useLocation();
  const selectedTab = Math.max(
    0,
    APPLICATION_TAB_KEYS.indexOf(applicationTab as ApplicationTab),
  );

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newValue: number) => {
      const newTab = APPLICATION_TAB_KEYS[newValue];
      const basePath = applicationTab
        ? location.pathname.replace(/\/[^/]+$/, `/${newTab}`)
        : `${location.pathname}/${newTab}`;
      navigate(basePath, { replace: true });
    },
    [navigate, location.pathname, applicationTab],
  );

  const {
    data: masResource,
    isLoading: isMasLoading,
    isError: isMasError,
  } = useMasResourceDetail(library ?? "", id ?? "");
  const { data: itemMeta } = useItem(library ?? "", "application", id ?? "");

  useEffect(() => {
    if (itemMeta?.resolved_id && id && itemMeta.resolved_id !== id) {
      navigate(
        scopeHref(library ?? "", `/applications/${encodeURIComponent(itemMeta.resolved_id)}`),
        { replace: true },
      );
    }
  }, [itemMeta?.resolved_id, id, library, navigate]);

  const initialYamlMap = useMemo<YamlOutputMap>(() => {
    if (!masResource) return {};

    const map: YamlOutputMap = {};
    if (!masResource.mas_yaml) return map;

    let masDoc: Record<string, unknown> | null;
    try {
      masDoc = parse(masResource.mas_yaml) as Record<string, unknown>;
    } catch {
      masDoc = null;
    }

    const kind = masDoc?.kind as string | undefined;

    // Single-agent sample apps (qa-agent) ship kind: Agent, not MAS.
    if (kind === "Agent") {
      const meta = masDoc?.metadata as { name?: string } | undefined;
      const name = meta?.name ?? id ?? "agent";
      map[`agent:${name}`] = masResource.mas_yaml;
    } else {
      map["mas"] = masResource.mas_yaml;
      for (const [agentName, yamlStr] of Object.entries(
        masResource.agents ?? {},
      )) {
        const doc = parse(yamlStr);
        const name = doc?.metadata?.name ?? agentName;
        map[`agent:${name}`] = yamlStr;
      }
    }
    return map;
  }, [masResource, id]);

  useEffect(() => {
    if (Object.keys(initialYamlMap).length > 0) {
      setYamlOutputMap(initialYamlMap);
    }
  }, [initialYamlMap]);

  const sortedKeys = useMemo(() => {
    const keys = Object.keys(yamlOutputMap);
    const masKey = keys.find((k) => k === "mas");
    const agentKeys = keys.filter((k) => k.startsWith("agent:")).sort();
    return masKey ? [masKey, ...agentKeys] : agentKeys;
  }, [yamlOutputMap]);

  useEffect(() => {
    if (!validateMutation.isSuccess && !validateMutation.isError) return;
    const timer = setTimeout(() => validateMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMutation.isSuccess, validateMutation.isError]);

  useEffect(() => {
    if (!validateMasMutation.isSuccess && !validateMasMutation.isError) return;
    const timer = setTimeout(() => validateMasMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMasMutation.isSuccess, validateMasMutation.isError]);

  const handleRunMas = useCallback(async () => {
    if (!library) return;
    const masYaml = yamlOutputMap["mas"];
    if (!masYaml) {
      setRunMasError("No MAS manifest available.");
      return;
    }

    const masDoc = parse(masYaml);
    const entryAgentId = masDoc?.spec?.workflow?.entry;
    if (!entryAgentId) {
      setRunMasError("MAS manifest has no workflow entry agent defined.");
      return;
    }

    const entryAgentYaml = yamlOutputMap[`agent:${entryAgentId}`];
    if (!entryAgentYaml) {
      setRunMasError(`Entry agent "${entryAgentId}" manifest not found.`);
      return;
    }

    const entryDoc = parse(entryAgentYaml);
    const textInput = entryDoc?.spec?.["x-text-input"];
    if (!textInput || !textInput.trim()) {
      setRunMasError(
        `A Text Input node with content must be connected to the entry agent "${entryAgentId}" before running the MAS.`,
      );
      return;
    }

    setRunMasError(null);
    setRunMasResult(null);
    setIsRunningMas(true);

    try {
      const { job_id } = await runMas({
        library,
        item: id,
        query: textInput.trim(),
      });

      const POLL_INTERVAL = 1500;
      let result = await pollJob(job_id);
      while (result.status === "pending" || result.status === "running") {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        result = await pollJob(job_id);
      }

      if (result.status !== "completed" || result.exit_code !== 0) {
        setRunMasResult({
          message: result.stderr || result.error || "MAS run failed.",
          severity: "error",
        });
      } else {
        const stderrErrors = result.stderr
          ? result.stderr
              .split("\n")
              .filter(
                (line: string) =>
                  /\bError[:\s]|\bfailed\b|\bnot found\b|\bTraceback\b/i.test(
                    line,
                  ) && !/tool\(.*\) Error:/i.test(line),
              )
              .join("\n")
              .trim()
          : "";
        if (stderrErrors) {
          setRunMasError(stderrErrors);
        }
        setRunMasResult({
          message:
            result.stdout ||
            result.response ||
            "MAS run completed successfully.",
          severity: "success",
        });
      }
    } catch (err) {
      setRunMasError(
        err instanceof Error ? err.message : "An unexpected error occurred.",
      );
    } finally {
      setIsRunningMas(false);
    }
  }, [library, yamlOutputMap, id]);

  const handleValidateMas = useCallback(() => {
    if (!library) return;
    const masYaml = yamlOutputMap["mas"];
    if (!masYaml) return;
    validateMasMutation.mutate({ library, manifest_yaml: masYaml });
  }, [yamlOutputMap, validateMasMutation, library]);

  const handleValidate = useCallback(() => {
    if (!selectedAgentName || !library) return;
    const yamlKey = `agent:${selectedAgentName}`;
    const manifestYaml = yamlOutputMap[yamlKey];
    if (!manifestYaml) return;
    validateMutation.mutate({ library, manifest_yaml: manifestYaml });
  }, [selectedAgentName, yamlOutputMap, validateMutation, library]);

  const handleOpenSaveDialog = useCallback(() => {
    setSaveError("");
    setMasName(id ?? "");
    const masSource = initialYamlMap["mas"] || yamlOutputMap["mas"];
    if (masSource) {
      const masDoc = parse(masSource);
      setMasDescription(masDoc?.metadata?.description ?? "");
      setMasIntent(masDoc?.intent?.summary ?? "");
    } else {
      setMasDescription("");
      setMasIntent("");
    }
    setSaveDialogOpen(true);
  }, [id, yamlOutputMap, initialYamlMap]);

  const handleCloseSaveDialog = useCallback(() => {
    setSaveDialogOpen(false);
    setSaveError("");
    setMasName("");
    setMasDescription("");
    setMasIntent("");
  }, []);

  const handleSave = useCallback(async () => {
    const trimmedName = masName.trim();
    if (!trimmedName) {
      setSaveError("MAS name is required.");
      return;
    }

    // Build final yamls with user-provided metadata
    const finalYamlMap: Record<string, string> = {};
    for (const key of sortedKeys) {
      if (key === "mas") {
        const masDoc = parse(yamlOutputMap[key]);
        masDoc.metadata.name = trimmedName;
        if (masDescription.trim()) {
          masDoc.metadata.description = masDescription.trim();
        } else {
          delete masDoc.metadata.description;
        }
        if (masIntent.trim()) {
          masDoc.intent = { summary: masIntent.trim() };
        } else {
          delete masDoc.intent;
        }
        finalYamlMap[key] = stringify(masDoc);
      } else {
        finalYamlMap[key] = yamlOutputMap[key];
      }
    }

    // Validate all manifests against schemas
    const validationErrors = validateManifests(finalYamlMap);
    if (validationErrors.length > 0) {
      const messages = validationErrors.map(
        (ve) =>
          `${ve.manifest}:\n${ve.errors.map((e) => `  • ${e}`).join("\n")}`,
      );
      setSaveError(messages.join("\n\n"));
      return;
    }

    const masYaml = finalYamlMap["mas"] ?? "";
    const agents: Record<string, string> = {};
    for (const key of sortedKeys) {
      if (key !== "mas") {
        agents[key.replace("agent:", "")] = finalYamlMap[key];
      }
    }

    setIsSaving(true);
    try {
      await updateMasResource({
        library: library ?? "",
        old_mas_name: id ?? "",
        mas_name: trimmedName,
        mas_yaml: masYaml,
        agents,
      });
      queryClient.invalidateQueries({ queryKey: ["apps", library] });
      queryClient.resetQueries({
        queryKey: ["apps", library, trimmedName],
      });
      handleCloseSaveDialog();

      if (trimmedName !== id) {
        navigate(scopeHref(library, `/applications/${trimmedName}`), { replace: true });
      }
    } catch (err) {
      setSaveError(
        err instanceof Error ? err.message : "Failed to save MAS resource.",
      );
    } finally {
      setIsSaving(false);
    }
  }, [
    masName,
    masDescription,
    masIntent,
    yamlOutputMap,
    sortedKeys,
    handleCloseSaveDialog,
    id,
    navigate,
    library,
    queryClient,
  ]);

  if (isMasLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (isMasError) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
          gap: 2,
        }}
      >
        <Typography variant="h6">Application not found</Typography>
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          The application &quot;{id}&quot; does not exist or has been deleted.
        </Typography>
        <Button
          variant="primary"
          onClick={() => navigate(scopeHref(library, "/applications"))}
        >
          Back to Applications
        </Button>
      </Box>
    );
  }

  return (
    <PageWithTitle
      title={
        <Stack
          direction="row"
          sx={{
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Stack direction="row" sx={{ alignItems: "center", gap: 2 }}>
            <Typography
              variant={"h5"}
              sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
            >
              {toTitleCase(itemMeta?.name ?? id ?? "")}
            </Typography>
            {(itemMeta?.versions?.length ?? 0) > 0 && (
              <Select
                size="small"
                value={itemMeta?.version || itemMeta?.latest || ""}
                onChange={(e) =>
                  navigate(
                    scopeHref(
                      library ?? "",
                      `/applications/${encodeURIComponent(`${itemMeta?.name}@${e.target.value}`)}`,
                    ),
                  )
                }
              >
                {itemMeta?.versions.map((v) => (
                  <MenuItem key={v} value={v}>
                    {v}
                  </MenuItem>
                ))}
              </Select>
            )}
            {itemMeta?.ref && (
              <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                {itemMeta.ref}
              </Typography>
            )}
          </Stack>
          <Stack direction="row" sx={{ gap: "8px" }}>
            {itemMeta && !itemMeta.writable && (
              <>
                <Alert severity="info">Read-only — copy into a writable scope.</Alert>
                <Button onClick={() => setCopyOpen(true)}>Copy into…</Button>
              </>
            )}
            {itemMeta && (
              <Button
                onClick={() => {
                  void forkItem(library ?? "", "application", id ?? "", {}).then((created) =>
                    navigate(scopeHref(library ?? "", `/applications/${encodeURIComponent(created.id)}`)),
                  );
                }}
              >
                New version
              </Button>
            )}
            <Button
              variant="primary"
              onClick={handleOpenSaveDialog}
              disabled={sortedKeys.length === 0}
            >
              Save
            </Button>
            <Button
              variant="primary"
              onClick={handleValidate}
              disabled={!selectedAgentName || validateMutation.isPending}
            >
              {validateMutation.isPending ? "Validating..." : "Validate"}
            </Button>
            <Button
              variant="primary"
              onClick={handleValidateMas}
              disabled={!yamlOutputMap["mas"] || validateMasMutation.isPending}
            >
              {validateMasMutation.isPending
                ? "Validating MAS..."
                : "Validate MAS"}
            </Button>
            <Button
              variant="primary"
              onClick={handleRunMas}
              disabled={isRunningMas}
            >
              {isRunningMas ? "Running MAS..." : "Run MAS"}
            </Button>
          </Stack>
        </Stack>
      }
    >
      <Stack direction={"column"} sx={{ width: "100%", height: "100%" }}>
        {validateMutation.isError && (
          <Alert
            severity="error"
            onClose={() => validateMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMutation.error.message}
          </Alert>
        )}
        {validateMutation.isSuccess && !validateMutation.isError && (
          <Alert
            severity={
              validateMutation.data.exit_code === 0 ? "success" : "error"
            }
            onClose={() => validateMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMutation.data.exit_code === 0
              ? validateMutation.data.stdout || "Validation passed."
              : validateMutation.data.stderr || validateMutation.data.stdout}
          </Alert>
        )}
        {validateMasMutation.isError && (
          <Alert
            severity="error"
            onClose={() => validateMasMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMasMutation.error.message}
          </Alert>
        )}
        {validateMasMutation.isSuccess && !validateMasMutation.isError && (
          <Alert
            severity={
              validateMasMutation.data.exit_code === 0 ? "success" : "error"
            }
            onClose={() => validateMasMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMasMutation.data.exit_code === 0
              ? validateMasMutation.data.stdout || "MAS validation passed."
              : validateMasMutation.data.stderr ||
                validateMasMutation.data.stdout}
          </Alert>
        )}
        {runMasError && (
          <Alert
            severity="error"
            onClose={() => setRunMasError(null)}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {runMasError}
          </Alert>
        )}
        {runMasResult && !runMasError && (
          <Alert
            severity={runMasResult.severity}
            onClose={() => setRunMasResult(null)}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {runMasResult.message}
          </Alert>
        )}
        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={selectedTab} onChange={handleTabChange}>
            <Tab label="Graph" id="application-tab-0" />
            <Tab label="Yaml" id="application-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={selectedTab} index={0} sx={{ flex: 1 }}>
          <Box sx={{ paddingTop: "8px" }}>
            <CanvasBuilder
              initialYamlMap={initialYamlMap}
              masName={id}
              onYamlChange={setYamlOutputMap}
              onAgentSelect={setSelectedAgentName}
            />
          </Box>
        </TabPanel>

        <TabPanel value={selectedTab} index={1} sx={{ flex: 1 }}>
          <Stack direction={"column"} sx={{ gap: "24px", paddingTop: "8px" }}>
            {sortedKeys.length === 0 && (
              <Typography
                variant="body2"
                sx={{ color: "text.secondary", py: 2 }}
              >
                No YAML manifests available.
              </Typography>
            )}
            {sortedKeys.map((key) => (
              <Box key={key}>
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontFamily: "monospace",
                    mb: 0.5,
                    color:
                      key === "mas"
                        ? theme.palette.warning.main
                        : theme.palette.info.main,
                  }}
                >
                  {key === "mas" ? "application" : key}
                </Typography>
                <CodeBlock code={yamlOutputMap[key]} language="yaml" />
              </Box>
            ))}
          </Stack>
        </TabPanel>
      </Stack>

      <Dialog
        open={saveDialogOpen}
        onClose={handleCloseSaveDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Save MAS</DialogTitle>
        <DialogContent>
          {saveError && (
            <Alert severity="error" sx={{ mb: 2, whiteSpace: "pre-wrap" }}>
              {saveError}
            </Alert>
          )}
          <Stack direction="column" sx={{ gap: "16px" }}>
            <InputField
              autoFocus
              label="MAS Name"
              placeholder="Enter a name for the MAS"
              autoComplete="off"
              fullWidth
              value={masName}
              onChange={(e) => {
                setMasName(e.target.value);
                setSaveError("");
              }}
            />
            <InputField
              label="Description"
              placeholder="Enter a description"
              autoComplete="off"
              fullWidth
              multiline
              minRows={2}
              value={masDescription}
              onChange={(e) => setMasDescription(e.target.value)}
            />
            <InputField
              label="Intent"
              placeholder="Enter the intent summary"
              autoComplete="off"
              fullWidth
              multiline
              minRows={2}
              value={masIntent}
              onChange={(e) => setMasIntent(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseSaveDialog}>Cancel</Button>
          <Button variant="gradient" onClick={handleSave} disabled={isSaving}>
            {isSaving ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
      <CreateItemDialog
        open={copyOpen}
        kind="application"
        scopeId={library ?? ""}
        copyFrom={id ? { scopeId: library ?? "", itemId: id } : undefined}
        onClose={() => setCopyOpen(false)}
        onCreated={(sid, createdId) =>
          navigate(scopeHref(sid, `/applications/${encodeURIComponent(createdId)}`))
        }
      />
    </PageWithTitle>
  );
};

export default Application;
