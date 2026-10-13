//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  ExperimentsTable,
  BenchmarkOps,
  TabPanel,
} from "@/components";
import type { ExperimentJobStatus } from "@/components/ExperimentsTable/ExperimentsTable";
import {
  Alert,
  Box,
  Button,
  Stack,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useNavigate, useParams } from "react-router";
import { parse } from "yaml";
import {
  useExperiments,
  type ExperimentSummary,
  fetchExperimentContent,
  deleteExperimentApi,
  runBenchmark,
  pollJob,
  fetchJobs,
  fetchJobDetail,
  deleteExperimentCache,
  downloadBenchmarkExport,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";

const POLL_INTERVAL = 2000;

const experimentJobKey = (exp: Pick<ExperimentSummary, "name" | "library">) =>
  exp.library ? `${exp.library}/${exp.name}` : exp.name;

const Experiments = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { library = "" } = useParams<{ library: string }>();
  const queryClient = useQueryClient();

  const { data: experiments = [], isLoading } = useExperiments(library);

  const experimentByKey = useMemo(() => {
    const map = new Map<string, ExperimentSummary>();
    for (const exp of experiments) {
      map.set(experimentJobKey(exp), exp);
    }
    return map;
  }, [experiments]);

  const [runningJobs, setRunningJobs] = useState<
    Record<string, ExperimentJobStatus>
  >({});
  const [alertMessage, setAlertMessage] = useState<{
    message: string;
    severity: "success" | "error";
  } | null>(null);
  const [tab, setTab] = useState(0);

  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const handleAdd = () => {
    navigate(`/${library}/experiments/new/setup`);
  };

  const resolveExperiment = useCallback(
    (name: string) => {
      const direct = experimentByKey.get(experimentJobKey({ name, library }));
      if (direct) return direct;
      return experiments.find((e) => e.name === name);
    },
    [experimentByKey, experiments, library],
  );

  const handleEdit = useCallback(
    (name: string) => {
      const exp = resolveExperiment(name);
      const expLibrary = exp?.library ?? library;
      navigate(
        `/${expLibrary}/experiments/${encodeURIComponent(name)}/edit/setup`,
      );
    },
    [library, navigate, resolveExperiment],
  );

  useEffect(() => {
    if (!alertMessage) return;
    const timer = setTimeout(() => setAlertMessage(null), 5000);
    return () => clearTimeout(timer);
  }, [alertMessage]);

  const handleDelete = useCallback(
    async (names: string[]) => {
      try {
        await Promise.all(
          names.map((name) => {
            const exp = resolveExperiment(name);
            return deleteExperimentApi(exp?.library ?? library, name);
          }),
        );
        queryClient.resetQueries({ queryKey: ["experiments"] });
      } catch (err) {
        setAlertMessage({
          message:
            err instanceof Error
              ? err.message
              : "Failed to delete experiment(s).",
          severity: "error",
        });
        throw err;
      }
    },
    [library, queryClient, resolveExperiment],
  );

  const startPolling = useCallback((jobKey: string, jobId: string) => {
    const poll = async () => {
      try {
        const job = await pollJob(jobId);
        const isTerminal =
          job.status === "completed" ||
          job.status === "failed" ||
          job.status === "cancelled" ||
          job.status === "timeout";

        setRunningJobs((prev) => ({
          ...prev,
          [jobKey]: {
            jobId,
            status: job.status,
            progress: isTerminal ? 100 : (prev[jobKey]?.progress ?? 0),
            stdout: job.stdout || undefined,
            stderr: job.stderr || undefined,
          },
        }));

        if (isTerminal) {
          delete pollTimers.current[jobKey];
        } else {
          pollTimers.current[jobKey] = setTimeout(poll, POLL_INTERVAL);
        }
      } catch {
        delete pollTimers.current[jobKey];
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[jobKey];
          return next;
        });
      }
    };

    pollTimers.current[jobKey] = setTimeout(poll, POLL_INTERVAL);
  }, []);

  useEffect(() => {
    const recoverJobs = async () => {
      try {
        const allJobs = [
          ...(await fetchJobs("pending")),
          ...(await fetchJobs("running")),
          ...(await fetchJobs("completed")),
          ...(await fetchJobs("failed")),
        ];

        const benchmarkJobs = allJobs
          .filter((j) => j.endpoint?.includes("/benchmark/run"))
          .sort(
            (a, b) =>
              new Date(b.created_at).getTime() -
              new Date(a.created_at).getTime(),
          );

        const seen = new Set<string>();
        for (const job of benchmarkJobs) {
          const detail = await fetchJobDetail(job.id);
          const body = detail.request_body as
            | Record<string, unknown>
            | undefined;
          const experimentYaml = body?.experiment_yaml as string | undefined;
          if (!experimentYaml) continue;

          let experimentName: string | undefined;
          try {
            const parsed = parse(experimentYaml) as Record<string, unknown>;
            const inner = (parsed?.experiment ?? parsed) as
              | Record<string, unknown>
              | undefined;
            experimentName = inner?.name as string | undefined;
          } catch {
            /* ignore parse errors */
          }
          if (!experimentName) experimentName = job.id;

          const jobLibrary =
            job.endpoint?.match(
              /\/api\/libraries\/([^/]+)\/benchmark\/run/,
            )?.[1] ?? library;
          const jobKey = experimentJobKey({
            name: experimentName,
            library: jobLibrary,
          });

          if (seen.has(jobKey)) continue;
          seen.add(jobKey);

          const isTerminal =
            job.status === "completed" ||
            job.status === "failed" ||
            job.status === "cancelled" ||
            job.status === "timeout";

          if (isTerminal) {
            setRunningJobs((prev) => ({
              ...prev,
              [jobKey]: {
                jobId: job.id,
                status: job.status as ExperimentJobStatus["status"],
                progress: 100,
                stdout: detail.stdout || undefined,
                stderr: detail.stderr || undefined,
              },
            }));
          } else if (!pollTimers.current[jobKey]) {
            setRunningJobs((prev) => ({
              ...prev,
              [jobKey]: {
                jobId: job.id,
                status: job.status as ExperimentJobStatus["status"],
                progress: 0,
              },
            }));
            startPolling(jobKey, job.id);
          }
        }
      } catch {
        /* server may be unreachable on first load */
      }
    };

    recoverJobs();
  }, [library, startPolling]);

  const handleRun = useCallback(
    async (name: string) => {
      const exp = resolveExperiment(name);
      const expLibrary = exp?.library ?? library;
      const jobKey = experimentJobKey({ name, library: expLibrary });

      setRunningJobs((prev) => ({
        ...prev,
        [jobKey]: { jobId: "", status: "pending", progress: 0 },
      }));

      try {
        const detail = await fetchExperimentContent(expLibrary, name);
        const { job_id } = await runBenchmark({
          library: expLibrary,
          experiment_yaml: detail.content,
        });

        setRunningJobs((prev) => ({
          ...prev,
          [jobKey]: { jobId: job_id, status: "running", progress: 0 },
        }));

        startPolling(jobKey, job_id);
      } catch {
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[jobKey];
          return next;
        });
      }
    },
    [library, resolveExperiment, startPolling],
  );

  const handleDeleteCache = useCallback(
    async (experimentName: string) => {
      try {
        await deleteExperimentCache(experimentName);
        queryClient.invalidateQueries({
          queryKey: ["experiment", experimentName],
        });
        setAlertMessage({
          message: `Cache deleted for "${experimentName}".`,
          severity: "success",
        });
      } catch (err) {
        setAlertMessage({
          message:
            err instanceof Error ? err.message : "Failed to delete cache.",
          severity: "error",
        });
      }
    },
    [library, queryClient],
  );

  const handleView = useCallback(
    (experimentName: string) => {
      const exp = resolveExperiment(experimentName);
      const expLibrary = exp?.library ?? library;
      navigate(
        `/${expLibrary}/experiments/${encodeURIComponent(experimentName)}`,
      );
    },
    [library, navigate, resolveExperiment],
  );

  const handleExport = useCallback(
    async (experimentName: string) => {
      try {
        await downloadBenchmarkExport(library, experimentName);
      } catch (err) {
        setAlertMessage({
          severity: "error",
          message: `Export failed: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    },
    [library],
  );

  return (
    <Box sx={{ position: "relative" }}>
      {alertMessage && (
        <Alert
          severity={alertMessage.severity}
          onClose={() => setAlertMessage(null)}
          sx={{
            whiteSpace: "pre-wrap",
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 1000,
          }}
        >
          {alertMessage.message}
        </Alert>
      )}
      <PageWithTitle
        title={
          <Stack
            direction="row"
            sx={{ gap: "8px", justifyContent: "space-between" }}
          >
            <Typography
              variant="h5"
              sx={{
                color: theme.palette.vars.interactivePrimaryDefaultDefault,
              }}
            >
              Experiments
            </Typography>
            {tab === 0 && <Button onClick={handleAdd}>Add Experiment</Button>}
          </Stack>
        }
      >
        <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 2 }}>
          <Tabs value={tab} onChange={(_e, v) => setTab(v)}>
            <Tab label="Experiments" id="experiments-tab-0" />
            <Tab label="Benchmark Utilities" id="experiments-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={tab} index={0}>
          <ExperimentsTable
            data={experiments}
            isLoading={isLoading}
            onEdit={handleEdit}
            onRun={handleRun}
            onExport={handleExport}
            onDelete={handleDelete}
            onDeleteCache={handleDeleteCache}
            onView={handleView}
            runningJobs={runningJobs}
            defaultHiddenColumns={["description", "version", "path", "library"]}
            showLibraryColumn
          />
        </TabPanel>

        <TabPanel value={tab} index={1}>
          <BenchmarkOps library={library} />
        </TabPanel>
      </PageWithTitle>
    </Box>
  );
};

export default Experiments;
