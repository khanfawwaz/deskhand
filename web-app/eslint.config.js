import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'public', 'src/api/schema.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
  {
    // the service worker source: a plain script that runs in a worker, with two placeholders filled in at build time
    files: ['pwa/sw.template.js'],
    languageOptions: { sourceType: 'script', globals: { ...globals.serviceworker, __PRECACHE__: 'readonly' } },
  },
  {
    // plain Node scripts (build checks, the fake server, screenshot helpers; some run code inside a page too)
    files: ['**/*.mjs'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
    rules: { 'no-redeclare': ['error', { builtinGlobals: false }] }, // a script may still list the globals it uses in a comment
  },
);
