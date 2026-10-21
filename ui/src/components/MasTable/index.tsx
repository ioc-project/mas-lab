//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useMemo } from "react";
import type { MASManifest } from "@/types/mas-types";
import { Stack } from "@mui/material";
import { MasTable } from "./MasTable";
import { useNavigate } from "react-router";
import { parse, stringify } from "yaml";
import { useMasResources, deleteMasResource, createMasResource } from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { scopeHref, useScopeId } from "@/api/scope";

interface MasTablePropsWrapper {
  defaultHiddenColumns?: string[];
  title?: string;
}

export const MasTableWrapper = ({
  defaultHiddenColumns = [],
  title,
}: MasTablePropsWrapper) => {
  const navigate = useNavigate();
  const library = useScopeId();
  const queryClient = useQueryClient();

  const { data: masResourceMap = {}, isLoading } = useMasResources(library);

  const masList = useMemo<MASManifest[]>(() => {
    return Object.entries(masResourceMap)
      .map(([itemId, entry]) => {
        try {
          const doc = parse(entry.mas_yaml) as {
            kind?: string;
            metadata?: MASManifest["metadata"];
            spec?: MASManifest["spec"];
          };
          const metadata = { ...(doc.metadata ?? {}), name: itemId };
          if (doc.kind === "Agent") {
            return {
              ...doc,
              kind: "MAS",
              metadata,
              spec: doc.spec ?? {},
            } as MASManifest;
          }
          return { ...doc, metadata } as MASManifest;
        } catch {
          return null;
        }
      })
      .filter((m): m is MASManifest => m !== null && !!m.metadata?.name);
  }, [masResourceMap]);

  const handleMasClick = (mas: MASManifest) => {
    navigate(scopeHref(library, `/applications/${mas.metadata?.name}`));
  };

  const handleDelete = useCallback(
    async (names: string[]) => {
      await Promise.all(names.map((name) => deleteMasResource(library, name)));
      queryClient.resetQueries({ queryKey: ["apps", library] });
    },
    [library, queryClient],
  );

  const handleDuplicate = useCallback(
    async (params: { masName: string; description: string; intent: string; sourceMasName: string }) => {
      const sourceEntry = masResourceMap[params.sourceMasName];
      if (!sourceEntry) {
        throw new Error(`Source application "${params.sourceMasName}" not found.`);
      }

      const masDoc = parse(sourceEntry.mas_yaml);
      masDoc.metadata.name = params.masName;
      if (params.description) {
        masDoc.metadata.description = params.description;
      } else {
        delete masDoc.metadata.description;
      }
      if (params.intent) {
        masDoc.intent = { summary: params.intent };
      } else {
        delete masDoc.intent;
      }
      const agentsList = masDoc.spec?.agency?.agents ?? masDoc.spec?.agents;
      if (agentsList) {
        for (const agent of agentsList) {
          if (agent.ref) {
            agent.ref = agent.ref.replace(params.sourceMasName, params.masName);
          }
        }
      }

      await createMasResource({
        library,
        mas_name: params.masName,
        mas_yaml: stringify(masDoc),
        agents: sourceEntry.agents ?? {},
      });
      queryClient.invalidateQueries({ queryKey: ["apps", library] });
    },
    [library, queryClient, masResourceMap],
  );

  return (
    <Stack direction="column" sx={{ gap: "8px", width: "100%" }}>
      <MasTable
        data={masList}
        isLoading={isLoading}
        isError={false}
        onMasClick={handleMasClick}
        onDelete={handleDelete}
        onDuplicate={handleDuplicate}
        defaultHiddenColumns={defaultHiddenColumns}
        title={title}
      />
    </Stack>
  );
};
