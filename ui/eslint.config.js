//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import { defineConfig, globalIgnores } from "eslint/config";

// Design-system guard: UI code must take components from `@open-ui-kit/core`
// (which also re-exports Box, Stack, Paper, IconButton, useTheme and ships its
// own icon set as `Icons`) instead of importing MUI directly. Direct imports
// bypass kit styling/behavior and make kit upgrades riskier.
//
// `@mui/icons-material` is intentionally NOT restricted: icons are plain SVGs with
// little upgrade risk. Prefer the kit's `Icons` when it has the icon you need.
//
// The allowlist below is the ONLY set of `@mui/material` symbols the kit does not
// provide. Before adding to it, check `@open-ui-kit/core` (docs: https://github.com/outshift-open/open-ui-kit) for an equivalent.
const KIT_MESSAGE =
  'Import this from "@open-ui-kit/core" instead of MUI directly. ' +
  "If the kit has no equivalent, add it to the allowlist in eslint.config.js with a reason.";

const muiMaterialAllowed = [
  // No kit equivalent (kit has no plain Alert; its Banner self-dismisses and takes
  // `text`/`status` instead of children/severity, so it is not a drop-in).
  "Alert",
  // Not exported by the kit.
  "Collapse",
  // Kept for the permanent left sidebar and the chat panel: the kit's `SideDrawer`
  // is a right-anchored closable detail panel, not a permanent nav drawer.
  "Drawer",
  // Used to build custom SVG icons (the kit does the same internally).
  "SvgIcon",
];

export default defineConfig([
  globalIgnores([
    "dist",
    "e2e",
    "src/api/generated",
    "src/types/agent-types.ts",
    "src/types/mas-types.ts",
    "src/test",
    "node_modules",
    ".yarn",
  ]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/refs": "off",
      "react-refresh/only-export-components": "off",
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@mui/material",
              message: KIT_MESSAGE,
              allowImportNames: muiMaterialAllowed,
            },
            {
              name: "@mui/material/styles",
              message: KIT_MESSAGE,
              allowImportNames: ["keyframes"],
            },
            {
              // The kit `Table` wraps material-react-table and re-exports its
              // state/column types as `Table*` (TableSortingState, TableColumnDef, …).
              name: "material-react-table",
              message:
                'Use `Table`/`TableProps` and the `Table*` types from "@open-ui-kit/core" instead of material-react-table.',
            },
            {
              // Includes `SystemStyleObject`, now re-exported by the kit.
              name: "@mui/system",
              message: KIT_MESSAGE,
            },
          ],
          patterns: [
            {
              // Deep imports (e.g. "@mui/material/Tooltip") dodge the path rules above.
              group: [
                "@mui/material/*",
                "!@mui/material/styles",
                "material-react-table/*",
              ],
              message:
                KIT_MESSAGE +
                " Deep MUI imports are not allowed; use a named import.",
            },
          ],
        },
      ],
    },
  },
]);
