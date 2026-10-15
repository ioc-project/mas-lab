//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useRef, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import {
  analyzeBenchmark,
  uploadImportBenchmark,
  pollJob,
  type JobSubmitResponse,
} from "@/api/apiCalls";
import { InputField, Button } from "@open-ui-kit/core";

interface BenchmarkOpsProps {
  library: string;
}

function formatAnalyzeOutput(stdout: string): string {
  const lines = stdout.split("\n");
  const kept: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^[═─]{3,}$/.test(trimmed)) continue;
    if (
      /^(BENCHMARK RESULTS|SCENARIO LEVEL|TEST LEVEL|RUN LEVEL)/.test(trimmed)
    )
      continue;
    if (/^(Scenario|Run)\s+/.test(trimmed) && /─{3,}/.test(trimmed)) continue;
    kept.push(trimmed);
  }
  return kept.join("\n");
}

export function BenchmarkOps({ library }: BenchmarkOpsProps) {
  const [benchmarkId, setBenchmarkId] = useState("last");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const runJob = useCallback(
    async (label: string, submit: () => Promise<JobSubmitResponse>) => {
      setBusy(true);
      setStatus(`${label}: submitting…`);
      try {
        const { job_id } = await submit();
        setStatus(`${label}: running…`);

        let result = await pollJob(job_id);
        while (result.status === "pending" || result.status === "running") {
          await new Promise((r) => setTimeout(r, 2000));
          result = await pollJob(job_id);
        }

        if (result.status === "completed" && result.stdout) {
          const summary =
            label === "Analyze"
              ? formatAnalyzeOutput(result.stdout)
              : result.stdout.trim().slice(-300);
          setStatus(`${label}: done\n${summary}`);
        } else if (result.status === "completed") {
          setStatus(`${label}: done`);
        } else {
          const errMsg = result.stderr
            ? result.stderr.trim().split("\n").pop() || ""
            : "";
          setStatus(
            `${label}: ${result.status}${errMsg ? ` — ${errMsg}` : ""}`,
          );
        }
      } catch (err) {
        setStatus(
          `${label} failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <Box
      sx={{
        p: 2,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography variant="body2" sx={{ mb: 2, color: "text.secondary" }}>
        Analyze or import benchmark run results. Use the benchmark name to
        reference a completed run. Export is available from the table row
        actions.
      </Typography>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ mb: 2 }}>
        <InputField
          label="Benchmark id"
          placeholder="short id, full id, experiment name, last, or latest"
          value={benchmarkId}
          onChange={(e) => setBenchmarkId(e.target.value)}
        />
      </Stack>
      <Stack
        direction="row"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
        alignItems="center"
      >
        <Button
          size="small"
          variant="gradient"
          disabled={busy || !benchmarkId.trim()}
          onClick={() =>
            runJob("Analyze", () =>
              analyzeBenchmark(library, { benchmark_id: benchmarkId.trim() }),
            )
          }
        >
          Analyze
        </Button>
        <Button
          size="small"
          variant="gradient"
          disabled={busy}
          onClick={() => fileInputRef.current?.click()}
        >
          Import
        </Button>
        {importFile && (
          <Typography variant="caption" sx={{ opacity: 0.7 }}>
            {importFile.name}
          </Typography>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".tar.gz,.tgz,.gz,application/gzip,application/x-gzip,application/x-tar"
          hidden
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setImportFile(file);
            e.target.value = "";
            runJob("Import", () => uploadImportBenchmark(library, file));
          }}
        />
      </Stack>
      {status && (
        <Box
          component="pre"
          sx={{
            mt: 1.5,
            p: 1.5,
            borderRadius: 1,
            bgcolor: "grey.900",
            color: "grey.100",
            fontSize: "0.75rem",
            fontFamily: "monospace",
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
            overflowY: "auto",
            m: 0,
          }}
        >
          {status}
        </Box>
      )}
    </Box>
  );
}
