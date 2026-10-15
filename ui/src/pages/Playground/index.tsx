//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  CodeBlock,
  TabPanel,
  CanvasBuilder,
} from "@/components";
import type { YamlOutputMap } from "@/components/CanvasBuilder/types";
import { Box, Stack, Typography, useTheme, Alert } from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { parse, stringify } from "yaml";
import { validateManifests } from "@/utils/manifestValidator";
import {
  useValidateAgent,
  useValidateMas,
  createMasResource,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  InputField,
  Tab,
  Tabs,
} from "@open-ui-kit/core";

const PLAYGROUND_TAB_KEYS = ["graph", "yaml"] as const;
type PlaygroundTab = (typeof PLAYGROUND_TAB_KEYS)[number];

const Playground = () => {
  const theme = useTheme();
  const { playgroundTab, library } = useParams();
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
  const [saveSuccess, setSaveSuccess] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const validateMutation = useValidateAgent();
  const validateMasMutation = useValidateMas();

  const navigate = useNavigate();
  const location = useLocation();
  const selectedTab = Math.max(
    0,
    PLAYGROUND_TAB_KEYS.indexOf(playgroundTab as PlaygroundTab),
  );

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newValue: number) => {
      const newTab = PLAYGROUND_TAB_KEYS[newValue];
      const basePath = playgroundTab
        ? location.pathname.replace(/\/[^/]+$/, `/${newTab}`)
        : `${location.pathname}/${newTab}`;
      navigate(basePath, { replace: true });
    },
    [navigate, location.pathname, playgroundTab],
  );

  const sortedKeys = useMemo(() => {
    const keys = Object.keys(yamlOutputMap);
    const masKey = keys.find((k) => k === "mas");
    const agentKeys = keys.filter((k) => k.startsWith("agent:")).sort();
    return masKey ? [masKey, ...agentKeys] : agentKeys;
  }, [yamlOutputMap]);

  useEffect(() => {
    if (!validateMutation.isSuccess && !validateMutation.isError) return;
    const timer = setTimeout(() => validateMutation.reset(), 2000);
    return () => clearTimeout(timer);
  }, [validateMutation.isSuccess, validateMutation.isError]);

  useEffect(() => {
    if (!validateMasMutation.isSuccess && !validateMasMutation.isError) return;
    const timer = setTimeout(() => validateMasMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMasMutation.isSuccess, validateMasMutation.isError]);

  const handleValidate = useCallback(() => {
    if (!selectedAgentName || !library) return;
    const yamlKey = `agent:${selectedAgentName}`;
    const manifestYaml = yamlOutputMap[yamlKey];
    if (!manifestYaml) return;
    validateMutation.mutate({ library, manifest_yaml: manifestYaml });
  }, [selectedAgentName, yamlOutputMap, validateMutation, library]);

  const handleValidateMas = useCallback(() => {
    if (!library) return;
    const masYaml = yamlOutputMap["mas"];
    if (!masYaml) return;
    validateMasMutation.mutate({ library, manifest_yaml: masYaml });
  }, [yamlOutputMap, validateMasMutation, library]);

  const handleOpenSaveDialog = useCallback(() => {
    setSaveError("");
    setMasName("");
    setMasDescription("");
    setMasIntent("");
    setSaveDialogOpen(true);
  }, []);

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

    const MAS_NAME_PLACEHOLDER = "__MAS_NAME__";

    const finalYamlMap: Record<string, string> = {};
    for (const key of sortedKeys) {
      if (key === "mas") {
        const masDoc = parse(yamlOutputMap[key]);
        masDoc.metadata.name = trimmedName;
        if (masDescription.trim()) {
          masDoc.metadata.description = masDescription.trim();
        }
        if (masIntent.trim()) {
          masDoc.intent = { summary: masIntent.trim() };
        }
        const agentsList = masDoc.spec?.agency?.agents ?? masDoc.spec?.agents;
        if (agentsList) {
          for (const agent of agentsList) {
            if (agent.ref) {
              agent.ref = agent.ref.replace(MAS_NAME_PLACEHOLDER, trimmedName);
            }
          }
        }
        finalYamlMap[key] = stringify(masDoc);
      } else {
        finalYamlMap[key] = yamlOutputMap[key];
      }
    }

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
      await createMasResource({
        library: library ?? "",
        mas_name: trimmedName,
        mas_yaml: masYaml,
        agents,
      });
      queryClient.resetQueries({ queryKey: ["apps", library] });
      handleCloseSaveDialog();
      setSaveSuccess(`"${trimmedName}" saved successfully.`);
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
    library,
    queryClient,
  ]);

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
          <Typography
            variant={"h5"}
            sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
          >
            Playground
          </Typography>
          <Stack direction="row" sx={{ gap: "8px" }}>
            <Button
              variant="gradient"
              onClick={handleOpenSaveDialog}
              disabled={sortedKeys.length === 0}
            >
              Save
            </Button>

            <Button
              variant="gradient"
              onClick={handleValidate}
              disabled={!selectedAgentName || validateMutation.isPending}
            >
              {validateMutation.isPending ? "Validating..." : "Validate"}
            </Button>

            <Button
              variant="gradient"
              onClick={handleValidateMas}
              disabled={!yamlOutputMap["mas"] || validateMasMutation.isPending}
            >
              {validateMasMutation.isPending
                ? "Validating MAS..."
                : "Validate MAS"}
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
        {saveSuccess && (
          <Alert
            severity="success"
            onClose={() => setSaveSuccess("")}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {saveSuccess}
          </Alert>
        )}
        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={selectedTab} onChange={handleTabChange}>
            <Tab label="Graph" id="playground-tab-0" />
            <Tab label="Yaml" id="playground-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={selectedTab} index={0} sx={{ flex: 1 }}>
          <Box sx={{ height: "100%", paddingTop: "8px" }}>
            <CanvasBuilder
              onYamlChange={setYamlOutputMap}
              onAgentSelect={setSelectedAgentName}
            />
          </Box>
        </TabPanel>

        <TabPanel value={selectedTab} index={1} sx={{ flex: 1 }}>
          <Stack direction={"column"} sx={{ gap: "24px" }}>
            {sortedKeys.length === 0 && (
              <Typography
                variant="body2"
                sx={{ color: "text.secondary", py: 2 }}
              >
                Add agent nodes to the graph to generate YAML manifests.
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
                  {key === "mas"
                    ? "mas.yaml"
                    : `${key.replace("agent:", "")}.agent.yaml`}
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
    </PageWithTitle>
  );
};

export default Playground;
