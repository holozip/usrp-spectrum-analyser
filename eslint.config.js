import js from '@eslint/js';
import hooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default [
  {ignores: ['build/**', 'test-results/**', 'playwright-report/**']},
  {files: ['**/*.{js,jsx}'], rules: js.configs.recommended.rules},
  {files: ['**/*.js'], languageOptions: {globals: globals.node}},
  {files: ['src/spectrum/frontend/tests/*.spec.js'], languageOptions: {globals: globals.browser}},
  {
    files: ['src/spectrum/frontend/src/**/*.{js,jsx}'],
    languageOptions: {globals: globals.browser, parserOptions: {ecmaFeatures: {jsx: true}}},
    rules: {
      'react-hooks/rules-of-hooks': hooks.configs.recommended.rules['react-hooks/rules-of-hooks'],
      'react-hooks/exhaustive-deps': hooks.configs.recommended.rules['react-hooks/exhaustive-deps'],
    },
    plugins: {'react-hooks': hooks},
  },
];
