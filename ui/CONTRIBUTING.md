<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->

# Contributing to the mas-lab UI

Conventions for working in `ui/`. For repo-wide rules (issues, PR process) see [../CONTRIBUTING.md](../CONTRIBUTING.md); for setup and the
backend see [README.md](README.md) and [../DEVELOPMENT.md](../DEVELOPMENT.md).

## Prerequisites

To make contributions to this project, the following system dependencies must be available:

- [Node Version Manager](https://github.com/nvm-sh/nvm)

From `ui/`, run `nvm install` once to fetch the Node version pinned in `.nvmrc`, and `nvm use` to
switch to it in each new shell. `engines` in `package.json` states the minimum supported version,
and the Docker build uses the same major version as `.nvmrc`.

## Design system

Take components, hooks, icons and types from the design-system package
[`@open-ui-kit/core`](https://github.com/outshift-open/open-ui-kit), not from `@mui/material`.
Importing MUI directly bypasses the kit's styling and behavior and makes kit upgrades riskier.

ESLint enforces this (`no-restricted-imports` in `eslint.config.js`). It blocks `@mui/material`,
deep imports such as `@mui/material/Tooltip`, `@mui/system` and `material-react-table`.

- **Check the kit first.** The kit re-exports many plain MUI components (`Box`, `Stack`,
  `IconButton`, `Typography`, …), so for those only the import changes. Some have a different name
  or API (for example `Spinner` instead of `CircularProgress`, `InputField` instead of
  `TextField`, `Tag` instead of `Chip`, `Header` instead of `AppBar`, `Table` instead of
  `material-react-table`). Look in the kit's source or Storybook rather than guessing the props.
- **Types come from the kit too** (`SxProps`, `Theme`, `SvgIconProps`, the `Table*` types, …).
- **Icons:** prefer the kit's `Icons`. `@mui/icons-material` is allowed when the kit lacks the icon
  you need; it is deliberately not restricted. Custom SVG icons live in `src/assets/icons`.
- **The allowlist** of MUI symbols that have no drop-in kit equivalent is `muiMaterialAllowed` in
  `eslint.config.js`, with a reason next to each entry. A drop-in replacement is one you can swap
  in by changing only the import; if the props or behavior differ, it is not one.
- **Adding to the allowlist:** only after checking the kit for an equivalent. Add the symbol with a
  one-line reason and mention it in your PR. Do not use `eslint-disable` comments to get around the
  rule.

Things to know when using kit components:

- The kit theme is required. Kit components read from the theme, so anything that renders them in
  a test must be wrapped in the kit `ThemeProvider` (see [Testing](#testing)). The provider already
  includes `CssBaseline`; do not add another.
- Be careful when you pass `slotProps` to a kit component. Some kit components spread caller props
  after their own defaults, so your `slotProps` can replace the kit's styling instead of merging
  with it. Check the component's source, and put layout styles on the content when in doubt.
- The kit's `Link` renders a router link, and uses `react-router-dom`. In tests, use
  `MemoryRouter` from `react-router-dom` so both share one router context.

## Buttons

Use the kit `Button` and always set `variant` explicitly so buttons look the same across pages.

- Actions (Save, Create, Run, Cancel, Back, …): `variant="gradient"`.
- Destructive actions (Delete): `variant="primary" color="negative"`. Do not make these gradient;
  the red colour is the signal.

## Tables

Use the kit `Table`, not `material-react-table`. Define the props as a typed object and spread it:

```tsx
import { Table, type TableProps } from "@open-ui-kit/core";

const tableProps: TableProps<Row> = {
  data,
  columns,
  isLoading,
  rowCount: data.length,
  title: { label: "" },
  enableRowActions: true,
  renderRowActionMenuItems: ({ row, closeMenu }) => [/* <MenuItem …/> */],
  muiTableBodyRowProps: ({ row }) => ({
    onClick: () =>
      navigate(
        scopeHref(library, `/things/${encodeURIComponent(row.original.name)}`),
      ),
    sx: { cursor: "pointer" },
  }),
};

return <Table {...tableProps} />;
```

- Put row navigation in `muiTableBodyRowProps.onClick`, not on a cell. The whole row is then
  clickable, and the row-action menu and select checkbox do not trigger it.
- Keep the `columns` array in `useMemo`. If a cell depends on changing state, list that state in
  the dependencies.
- Use the table types exported by the kit (`TableColumnDef`, `TableSortingState`, …) for state and
  column definitions.

## Routing and scopes

URLs are scoped: `/:scopeType/:scope/...`, where `scopeType` is `labs` or `libraries`.

- Do not build these URLs by hand. Use `scopeHref()` and `useScopeId()` from `src/api/scope.ts`,
  and encode dynamic path segments with `encodeURIComponent`.
- Declare every route once, in `src/routes/routes.tsx`.
- If a page keeps its tab in the URL (for example `…/edit/yaml`), register a matching route for
  every base route that uses it, including the create variant. A URL that matches no route falls
  into the catch-all and renders a 404 or a blank page.
- Breadcrumbs are off by default (`PageWithTitle`). Enable them on a page only if every crumb
  points at a route that exists.

## Page layout and scrolling

Wrap pages in `PageWithTitle`. It provides the title, an actions slot and a scrolling container.
Leave that scrolling alone unless you need a fixed viewport:

- Tall content (code blocks, long forms) works with the default: the whole page scrolls. A child's
  own `overflow: auto` does nothing unless an ancestor has a bounded height.
- `overflow: "hidden"` on the page clips anything taller than the window. A child can only scroll
  itself if every flex ancestor up to a fixed height has `minHeight: 0`. Avoid this unless you
  really need it.
- For canvas or graph tabs, follow an existing page that already does it (a `TabPanel` with
  `flex: 1` wrapping a box with `height: "100%"`).

Show YAML with the shared `CodeBlock`, use `TabPanel` for tab bodies, and derive the selected tab
from the URL rather than from local state.

## Data and API access

- Talk to the controller only through the modules in `src/api/` and TanStack Query. Do not call
  `fetch` from components.
- Do not edit `src/api/generated/`; regenerate it (`yarn generate:api`, `yarn generate:types`).
- No filesystem paths or model names in UI code. `scripts/check-no-paths.mjs` (run by
  `yarn lint`) fails on literals such as `"mas.yaml"` or `gpt-`. Get names and layouts from the
  API.

## Testing

Tests use vitest, jsdom and MSW (`src/test/`). Unhandled requests fail the test
(`onUnhandledRequest: "error"`), so add a handler for every endpoint your component calls.

Shared handlers live in `src/test/handlers.ts` and apply to every test. Add an endpoint there if
many tests need it. For a response that only one test needs, override it with `server.use(...)`;
the override is dropped after each test (`server.resetHandlers()` in `src/test/setup.ts`):

```tsx
import { http, HttpResponse } from "msw";
import { server } from "../test/server";

it("shows an empty state when the lab has no experiments", async () => {
  server.use(
    http.get("http://localhost:8090/api/scopes/:scopeId/items", () =>
      HttpResponse.json({ items: [] }),
    ),
  );

  // render the component, then assert on the empty state
});
```

Components built on the kit need the theme, and a router if they link:

```tsx
import { ThemeMode, ThemeProvider } from "@open-ui-kit/core";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

render(
  <ThemeProvider defaultMode={ThemeMode.Midnight}>
    <MemoryRouter>
      <MyComponent />
    </MemoryRouter>
  </ThemeProvider>,
);
```

Without `ThemeProvider` a test usually crashes with `Cannot read properties of undefined` on a
theme token. Add a test for behavior you add or fix; existing `*.test.tsx` files show the pattern.

## Reporting issues

When submitting a new issue, please:

- Search existing issues to avoid duplicates.
- Use the issue templates provided.
- Include reproduction steps for bugs.
- Provide environment details (Node.js version, browser, OS).
- Add screenshots or recordings if applicable.

## Pull requests

Run these from `ui/` before pushing:

```bash
yarn tsc -b          # type check (also part of `yarn build`)
yarn lint            # eslint + scripts/check-no-paths.mjs
yarn test            # vitest
```

Then open the pages you touched in the browser (`yarn dev` plus `mas-lab serve`, see the README).
Type checks and unit tests do not catch styling or layout regressions.

Every source file starts with the Cisco copyright and `SPDX-License-Identifier: Apache-2.0`
header. Copy it from a neighbouring file.

- **Title:** Conventional Commits `fix(ui): …`, `refactor(ui): …`, `feat(ui): …`.
- **Keep refactors separate from behavior changes.** A mechanical import swap is easy to review;
  one that also changes routes or styling is not. Prefer a separate PR (or at least a
  separate commit) for bugs you find along the way.
- **Upgrade `@open-ui-kit/core` in its own PR.** A kit upgrade can shift styling across the whole
  app.
- **Mention what you checked** in the browser, and list anything you could not verify.
- **Call out additions to the MUI import allowlist** (`muiMaterialAllowed` in `eslint.config.js`)
  and why the kit has no equivalent.
