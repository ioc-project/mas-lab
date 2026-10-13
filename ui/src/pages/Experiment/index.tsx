//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle } from "@/components";
import { Box, CircularProgress, Typography, useTheme, Button } from "@mui/material";
import { useNavigate, useParams } from "react-router";
import { useCallback, useMemo, useState } from "react";
import { useExperimentDetail, fetchExperimentFile } from "@/api/apiCalls";
import {
  Panel,
  Group as PanelGroup,
  Separator as PanelResizeHandle,
} from "react-resizable-panels";
import {
  FilePreview,
  GroupedResults,
  ResultsOverviewCards,
} from "./components";
import { groupOutputs, hiddenSummary } from "./utils";

const Experiment = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { library = "", id = "" } = useParams<{
    library: string;
    id: string;
  }>();
  const {
    data: experiment,
    isLoading,
    isError,
    isRunning,
  } = useExperimentDetail(id);

  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const { plots, outputFiles, runGroups, useGrouped } = useMemo(
    () => groupOutputs(experiment?.tree ?? []),
    [experiment],
  );

  const allFilesHint = useMemo(
    () => hiddenSummary(experiment?.tree ?? []),
    [experiment],
  );

  const handleFileSelect = useCallback(
    async (path: string) => {
      setSelectedFile(path);
      setFileContent(null);
      setFileLoading(true);
      try {
        const result = await fetchExperimentFile(id, path);
        setFileContent(result.content);
      } catch {
        setFileContent("Error loading file.");
      } finally {
        setFileLoading(false);
      }
    },
    [id],
  );

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "50vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (isError || !experiment) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 2,
          mt: 8,
        }}
      >
        <Typography variant="h6">
          Experiment not found or not yet executed.
        </Typography>
        <Button onClick={() => navigate(`/${library}/experiments`)}>
          Back to Experiments
        </Button>
      </Box>
    );
  }

  return (
    <PageWithTitle
      title={
        <Typography
          variant="h5"
          sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
        >
          {experiment.name}
        </Typography>
      }
    >
      {useGrouped && (
        <ResultsOverviewCards
          metadata={experiment.metadata}
          plots={plots}
          outputFiles={outputFiles}
          runGroups={runGroups}
          running={isRunning}
        />
      )}
      <Box sx={{ height: "calc(100vh - 200px)", overflow: "hidden" }}>
        <PanelGroup orientation="horizontal">
          <Panel defaultSize={400} minSize={200} maxSize={400}>
            <Box sx={{ height: "100%", overflow: "auto", pr: 1 }}>
              <GroupedResults
                experimentId={id}
                tree={experiment.tree}
                plots={plots}
                outputFiles={outputFiles}
                runGroups={runGroups}
                allFilesHint={allFilesHint}
                useGrouped={useGrouped}
                selectedPath={selectedFile}
                onSelect={handleFileSelect}
              />
            </Box>
          </Panel>

          <PanelResizeHandle
            style={{
              width: 4,
              backgroundColor: "transparent",
              cursor: "col-resize",
              borderLeft: "1px solid var(--mui-palette-divider, #444)",
              transition: "background-color 0.15s",
            }}
            className="experiment-resize-handle"
          />

          <Panel>
            <Box sx={{ height: "100%", overflow: "auto", pl: 2 }}>
              <FilePreview
                selectedFile={selectedFile}
                fileContent={fileContent}
                fileLoading={fileLoading}
              />
            </Box>
          </Panel>
        </PanelGroup>
      </Box>
    </PageWithTitle>
  );
};

export default Experiment;
