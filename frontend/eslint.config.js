import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const HEBREW = '/[\\u0590-\\u05FF]/';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'src/api/schema.d.ts', 'playwright-report', 'test-results', '*.cjs'] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-non-null-assertion': 'error',
      'no-restricted-syntax': [
        'error',
        // DESIGN_SYSTEM §1 / CODE_STYLE §3.3 — all Hebrew copy lives in src/i18n
        { selector: `Literal[value=${HEBREW}]`, message: 'Hebrew text belongs in src/i18n/he.ts' },
        { selector: `TemplateElement[value.raw=${HEBREW}]`, message: 'Hebrew text belongs in src/i18n/he.ts' },
        { selector: `JSXText[value=${HEBREW}]`, message: 'Hebrew text belongs in src/i18n/he.ts' },
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: 'dangerouslySetInnerHTML is banned' },
      ],
    },
  },
  {
    files: ['src/i18n/**', 'e2e/**', 'src/**/*.test.{ts,tsx}'],
    rules: { 'no-restricted-syntax': 'off' },
  },
);
