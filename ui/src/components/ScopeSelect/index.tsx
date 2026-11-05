//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  InputField,
  MenuItem,
  MenuSubheader,
  Select,
  type SelectChangeEvent,
} from "@open-ui-kit/core";
import type { ReactNode } from "react";

import { groupScopes, LABS_CATALOG_ID, type Scope } from "@/api/scopes";

type Props = {
  scopes: Scope[];
  value: string;
  onChange: (id: string) => void;
  writableOnly?: boolean;
  kind?: string;
  label?: string;
  size?: "small";
  minWidth?: number;
  allowCatalog?: boolean;
};

function allowScope(
  scope: Scope,
  writableOnly?: boolean,
  kind?: string,
): boolean {
  if (kind && !scope.kinds.includes(kind)) return false;
  if (writableOnly && !scope.writable) return false;
  return true;
}

function menuLabel(scope: Scope): string {
  return `${scope.title}${scope.writable ? "" : " (read-only)"}`;
}

export function ScopeSelect({
  scopes,
  value,
  onChange,
  writableOnly,
  kind,
  label,
  size = "small",
  minWidth = 220,
  allowCatalog,
}: Props) {
  const { labs, libraries, workspace, local, packaged } = groupScopes(scopes);
  const allow = (scope: Scope) => allowScope(scope, writableOnly, kind);

  const items: ReactNode[] = [];
  if (allowCatalog) {
    items.push(
      <MenuItem
        key={LABS_CATALOG_ID}
        value={LABS_CATALOG_ID}
        sx={{ fontSize: "13px" }}
      >
        All labs
      </MenuItem>,
    );
  }

  const labNodes = labs
    .map((lab) => {
      const children = (lab.libraries ?? [])
        .map((id) => libraries.find((lib) => lib.id === id))
        .filter((child): child is Scope => !!child && allow(child));
      const labOk = allow(lab);
      if (!labOk && children.length === 0) return null;
      return { lab, labOk, children };
    })
    .filter(
      (node): node is { lab: Scope; labOk: boolean; children: Scope[] } =>
        node !== null,
    );

  if (labNodes.length > 0) {
    items.push(<MenuSubheader key="labs">Labs</MenuSubheader>);
    for (const { lab, labOk, children } of labNodes) {
      items.push(
        <MenuItem
          key={lab.id}
          value={lab.id}
          disabled={!labOk}
          sx={{ fontSize: "13px" }}
        >
          {menuLabel(lab)}
        </MenuItem>,
      );
      for (const child of children) {
        items.push(
          <MenuItem
            key={child.id}
            value={child.id}
            sx={{ fontSize: "13px", pl: 4 }}
          >
            {menuLabel(child)}
          </MenuItem>,
        );
      }
    }
  }

  const workspaceItems = workspace.filter(allow);
  if (workspaceItems.length > 0) {
    items.push(<MenuSubheader key="workspace">Workspace</MenuSubheader>);
    for (const scope of workspaceItems) {
      items.push(
        <MenuItem key={scope.id} value={scope.id} sx={{ fontSize: "13px" }}>
          {menuLabel(scope)}
        </MenuItem>,
      );
    }
  }

  const localItems = local.filter(allow);
  if (localItems.length > 0) {
    items.push(<MenuSubheader key="local">Local libraries</MenuSubheader>);
    for (const scope of localItems) {
      items.push(
        <MenuItem key={scope.id} value={scope.id} sx={{ fontSize: "13px" }}>
          {menuLabel(scope)}
        </MenuItem>,
      );
    }
  }

  const packagedItems = writableOnly ? [] : packaged.filter(allow);
  if (packagedItems.length > 0) {
    items.push(
      <MenuSubheader key="packaged">Packaged (read-only)</MenuSubheader>,
    );
    for (const scope of packagedItems) {
      items.push(
        <MenuItem key={scope.id} value={scope.id} sx={{ fontSize: "13px" }}>
          {menuLabel(scope)}
        </MenuItem>,
      );
    }
  }

  const allowedIds: string[] = allowCatalog ? [LABS_CATALOG_ID] : [];
  for (const node of labNodes) {
    if (node.labOk) allowedIds.push(node.lab.id);
    for (const child of node.children) allowedIds.push(child.id);
  }
  for (const scope of workspaceItems) allowedIds.push(scope.id);
  for (const scope of localItems) allowedIds.push(scope.id);
  for (const scope of packagedItems) allowedIds.push(scope.id);
  const safeValue = allowedIds.includes(value)
    ? value
    : (allowedIds[0] ?? value ?? "");
  if (allowedIds.length === 0) {
    items.push(
      <MenuItem
        key="_none"
        value={safeValue}
        disabled
        sx={{ fontSize: "13px" }}
      >
        {scopes.length === 0 ? "Loading…" : "No writable scopes for this kind"}
      </MenuItem>,
    );
  }

  const handleChange = (event: SelectChangeEvent<string>) => {
    onChange(event.target.value);
  };

  if (label) {
    return (
      <InputField
        select
        label={label}
        size={size}
        value={safeValue}
        onChange={(event) => onChange(event.target.value)}
        fullWidth
      >
        {items}
      </InputField>
    );
  }

  return (
    <Select
      size={size}
      value={safeValue}
      onChange={handleChange}
      variant="outlined"
      clearable={false}
      sx={{
        minWidth,
        fontSize: "13px",
        height: "28px",
        "& .MuiSelect-select": { py: "2px" },
      }}
    >
      {items}
    </Select>
  );
}
