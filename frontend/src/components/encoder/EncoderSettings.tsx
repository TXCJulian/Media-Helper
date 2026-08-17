import { useEffect, useRef, useState } from 'react'
import PresetEditor from '@/components/encoder/PresetEditor'
import {
  reprocessEncoderFile,
  saveEncoderConfig,
  saveEncoderRules,
  testEncoderFile,
} from '@/lib/api'
import type {
  EncoderConfig,
  EncoderPreset,
  EncoderRule,
  EncoderRuleCondition,
  EncoderTestResult,
} from '@/types'

type RuleSet = {
  rules: EncoderRule[]
  fallback: string
}

export interface EncoderSettingsProps {
  config: EncoderConfig
  presets: EncoderPreset[]
  rules: RuleSet
  onRefresh: () => void
  onError: (message: string) => void
}

type Section = 'watch' | 'presets' | 'rules'

const FIELDS = [
  'height',
  'width',
  'size',
  'bit_rate',
  'bit_depth',
  'frame_rate',
  'duration',
  'video_codec',
  'profile',
  'source_tool',
  'encoder_tag',
  'hdr',
  'dolby_vision',
]
const OPERATORS = ['>=', '<=', '>', '<', '==', '!=', 'contains']
const BOOLEAN_FIELDS = new Set(['hdr', 'dolby_vision'])
const NUMERIC_FIELDS = new Set([
  'height',
  'width',
  'size',
  'bit_rate',
  'bit_depth',
  'frame_rate',
  'duration',
])

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function cloneRules(rules: EncoderRule[]): EncoderRule[] {
  return rules.map((rule) => ({
    ...rule,
    conditions: rule.conditions.map((condition) => ({ ...condition })),
  }))
}

function parseConditionValue(field: string, value: string): unknown {
  if (BOOLEAN_FIELDS.has(field)) return value === 'true'
  if (NUMERIC_FIELDS.has(field)) {
    const parsed = Number(value)
    return value.trim() !== '' && Number.isFinite(parsed) ? parsed : value
  }
  return value
}

function initialCondition(): EncoderRuleCondition {
  return { field: 'height', op: '>=', value: 2160 }
}

function operatorsFor(field: string): string[] {
  if (BOOLEAN_FIELDS.has(field)) return ['==', '!=']
  if (NUMERIC_FIELDS.has(field)) return OPERATORS.filter((operator) => operator !== 'contains')
  return OPERATORS
}

function nextRuleId(rules: EncoderRule[]): string {
  const used = new Set(rules.map((rule) => rule.id.trim()))
  let suffix = 1
  while (used.has(`rule-${suffix}`)) suffix += 1
  return `rule-${suffix}`
}

function SectionButton({
  label,
  section,
  open,
  onToggle,
}: {
  label: string
  section: Section
  open: boolean
  onToggle: (section: Section) => void
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={() => onToggle(section)}
      className={`rounded-lg px-3 py-2 text-[0.78rem] font-medium transition ${
        open ? 'bg-teal-400/15 text-teal-300' : 'bg-white/4 text-[var(--text-secondary)]'
      }`}
    >
      {label}
    </button>
  )
}

export default function EncoderSettings({
  config,
  presets,
  rules,
  onRefresh,
  onError,
}: EncoderSettingsProps) {
  const [openSection, setOpenSection] = useState<Section | null>(null)
  const [watchPaths, setWatchPaths] = useState(() => [...config.watch_paths])
  const [draftRules, setDraftRules] = useState(() => cloneRules(rules.rules))
  const [fallback, setFallback] = useState(rules.fallback)
  const [savingPaths, setSavingPaths] = useState(false)
  const [savingRules, setSavingRules] = useState(false)
  const [testPath, setTestPath] = useState('')
  const [testResult, setTestResult] = useState<EncoderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [reprocessing, setReprocessing] = useState(false)
  const [reprocessMessage, setReprocessMessage] = useState<string | null>(null)
  const testPathRef = useRef('')
  const testRequestRef = useRef(0)
  const reprocessRequestRef = useRef(0)

  useEffect(() => setWatchPaths([...config.watch_paths]), [config.watch_paths])
  useEffect(() => {
    setDraftRules(cloneRules(rules.rules))
    setFallback(rules.fallback)
  }, [rules])

  const toggle = (section: Section) => {
    setOpenSection((current) => (current === section ? null : section))
  }

  const savePaths = async () => {
    const paths = watchPaths.map((path) => path.trim()).filter(Boolean)
    setSavingPaths(true)
    try {
      await saveEncoderConfig(paths)
      setWatchPaths(paths)
      onRefresh()
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setSavingPaths(false)
    }
  }

  const updateRule = (index: number, patch: Partial<EncoderRule>) => {
    setDraftRules((current) =>
      current.map((rule, ruleIndex) => (ruleIndex === index ? { ...rule, ...patch } : rule)),
    )
  }

  const updateCondition = (
    ruleIndex: number,
    conditionIndex: number,
    patch: Partial<EncoderRuleCondition>,
  ) => {
    setDraftRules((current) =>
      current.map((rule, index) =>
        index === ruleIndex
          ? {
              ...rule,
              conditions: rule.conditions.map((condition, itemIndex) =>
                itemIndex === conditionIndex ? { ...condition, ...patch } : condition,
              ),
            }
          : rule,
      ),
    )
  }

  const moveRule = (index: number, offset: -1 | 1) => {
    setDraftRules((current) => {
      const destination = index + offset
      if (destination < 0 || destination >= current.length) return current
      const next = [...current]
      ;[next[index], next[destination]] = [next[destination]!, next[index]!]
      return next
    })
  }

  const saveRules = async () => {
    const normalizedRules = draftRules.map((rule) => ({ ...rule, id: rule.id.trim() }))
    const ids = normalizedRules.map((rule) => rule.id)
    if (ids.some((id) => !id)) {
      onError('Rule IDs cannot be empty.')
      return
    }
    if (new Set(ids).size !== ids.length) {
      onError('Rule IDs must be unique.')
      return
    }

    setSavingRules(true)
    try {
      await saveEncoderRules({ rules: normalizedRules, fallback })
      onRefresh()
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setSavingRules(false)
    }
  }

  const runTest = async () => {
    const path = testPath.trim()
    if (!path) return
    const request = ++testRequestRef.current
    setTesting(true)
    setTestResult(null)
    setReprocessMessage(null)
    try {
      const result = await testEncoderFile(path)
      if (request === testRequestRef.current && testPathRef.current.trim() === path) {
        setTestResult(result)
      }
    } catch (error) {
      if (request === testRequestRef.current && testPathRef.current.trim() === path) {
        onError(errorMessage(error))
      }
    } finally {
      if (request === testRequestRef.current) setTesting(false)
    }
  }

  const reprocess = async () => {
    const path = testPath.trim()
    if (!path) return
    const request = ++reprocessRequestRef.current
    setReprocessing(true)
    setReprocessMessage(null)
    try {
      const result = await reprocessEncoderFile(path)
      if (request === reprocessRequestRef.current && testPathRef.current.trim() === path) {
        setReprocessMessage(
          result.cleared
            ? 'File queued for reconsideration on the next scan.'
            : 'File was not in the processed-file index.',
        )
      }
    } catch (error) {
      if (request === reprocessRequestRef.current && testPathRef.current.trim() === path) {
        onError(errorMessage(error))
      }
    } finally {
      if (request === reprocessRequestRef.current) setReprocessing(false)
    }
  }

  const changeTestPath = (path: string) => {
    testPathRef.current = path
    testRequestRef.current += 1
    reprocessRequestRef.current += 1
    setTestPath(path)
    setTestResult(null)
    setReprocessMessage(null)
    setTesting(false)
    setReprocessing(false)
  }

  const targets = ['skip', ...presets.map((preset) => preset.name)]

  return (
    <section aria-label="Encoder settings" className="glass rounded-[16px] p-4">
      <div className="flex flex-wrap gap-2">
        <SectionButton
          label="Watch Folders"
          section="watch"
          open={openSection === 'watch'}
          onToggle={toggle}
        />
        <SectionButton
          label="Presets"
          section="presets"
          open={openSection === 'presets'}
          onToggle={toggle}
        />
        <SectionButton
          label="Rules"
          section="rules"
          open={openSection === 'rules'}
          onToggle={toggle}
        />
      </div>

      {openSection === 'watch' && (
        <div className="mt-4 space-y-3 border-t border-white/6 pt-4">
          {watchPaths.length === 0 && (
            <p className="text-[0.76rem] text-[var(--text-tertiary)]">
              Watching is paused until a folder is added and saved.
            </p>
          )}
          {watchPaths.map((path, index) => (
            <div key={index} className="flex items-end gap-2">
              <label className="min-w-0 flex-1 text-[0.72rem] text-[var(--text-secondary)]">
                Watch folder {index + 1}
                <input
                  value={path}
                  onChange={(event) =>
                    setWatchPaths((current) =>
                      current.map((item, itemIndex) =>
                        itemIndex === index ? event.target.value : item,
                      ),
                    )
                  }
                  className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-[0.82rem] text-[var(--text-primary)]"
                />
              </label>
              <button
                type="button"
                aria-label={`Remove watch folder ${index + 1}`}
                onClick={() =>
                  setWatchPaths((current) => current.filter((_, itemIndex) => itemIndex !== index))
                }
                className="rounded-lg px-3 py-2 text-[0.75rem] text-red-300"
              >
                Remove
              </button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setWatchPaths((current) => [...current, ''])}
              className="rounded-lg border border-white/8 px-3 py-2 text-[0.75rem] text-[var(--text-secondary)]"
            >
              Add watch folder
            </button>
            <button
              type="button"
              disabled={savingPaths}
              onClick={() => void savePaths()}
              className="rounded-lg bg-teal-500/15 px-3 py-2 text-[0.75rem] font-medium text-teal-300 disabled:opacity-50"
            >
              {savingPaths ? 'Saving…' : 'Save watch folders'}
            </button>
          </div>
        </div>
      )}

      {openSection === 'presets' && (
        <div className="mt-4 border-t border-white/6 pt-4">
          <PresetEditor
            presets={presets}
            onSaved={onRefresh}
            onDeleted={onRefresh}
            onError={onError}
          />
        </div>
      )}

      {openSection === 'rules' && (
        <div className="mt-4 space-y-4 border-t border-white/6 pt-4">
          <ol aria-label="Encoding rules" className="space-y-3">
            {draftRules.map((rule, ruleIndex) => (
              <li key={ruleIndex} className="rounded-xl border border-white/8 bg-white/3 p-3">
                <div className="flex flex-wrap items-end gap-2">
                  <label className="min-w-[10rem] flex-1 text-[0.7rem] text-[var(--text-secondary)]">
                    Rule name
                    <input
                      aria-label={`Rule ${ruleIndex + 1} name`}
                      value={rule.id}
                      onChange={(event) => updateRule(ruleIndex, { id: event.target.value })}
                      className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-2.5 py-2 text-[0.78rem] text-[var(--text-primary)]"
                    />
                  </label>
                  <button
                    type="button"
                    disabled={ruleIndex === 0}
                    aria-label={`Move rule ${rule.id} up`}
                    onClick={() => moveRule(ruleIndex, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={ruleIndex === draftRules.length - 1}
                    aria-label={`Move rule ${rule.id} down`}
                    onClick={() => moveRule(ruleIndex, 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove rule ${rule.id}`}
                    onClick={() =>
                      setDraftRules((current) =>
                        current.filter((_, itemIndex) => itemIndex !== ruleIndex),
                      )
                    }
                    className="text-[0.72rem] text-red-300"
                  >
                    Remove
                  </button>
                </div>

                <div className="mt-3 space-y-2">
                  {rule.conditions.map((condition, conditionIndex) => (
                    <div key={conditionIndex}>
                      {conditionIndex > 0 && (
                        <p className="mb-2 text-[0.62rem] font-semibold tracking-[0.12em] text-teal-300">
                          AND
                        </p>
                      )}
                      <div className="grid gap-2 sm:grid-cols-[1fr_5rem_1fr_auto]">
                        <label className="text-[0.68rem] text-[var(--text-tertiary)]">
                          Field
                          <select
                            aria-label={`${rule.id} condition ${conditionIndex + 1} field`}
                            value={condition.field}
                            onChange={(event) => {
                              const field = event.target.value
                              updateCondition(ruleIndex, conditionIndex, {
                                field,
                                op:
                                  BOOLEAN_FIELDS.has(field) ||
                                  (NUMERIC_FIELDS.has(field) && condition.op === 'contains')
                                    ? '=='
                                    : condition.op,
                                value: BOOLEAN_FIELDS.has(field)
                                  ? true
                                  : NUMERIC_FIELDS.has(field)
                                    ? 0
                                    : '',
                              })
                            }}
                          >
                            {FIELDS.map((field) => (
                              <option key={field} value={field}>
                                {field}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="text-[0.68rem] text-[var(--text-tertiary)]">
                          Operator
                          <select
                            aria-label={`${rule.id} condition ${conditionIndex + 1} operator`}
                            value={condition.op}
                            onChange={(event) =>
                              updateCondition(ruleIndex, conditionIndex, { op: event.target.value })
                            }
                          >
                            {operatorsFor(condition.field).map((operator) => (
                              <option key={operator} value={operator}>
                                {operator}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="text-[0.68rem] text-[var(--text-tertiary)]">
                          Value
                          {BOOLEAN_FIELDS.has(condition.field) ? (
                            <select
                              aria-label={`${rule.id} condition ${conditionIndex + 1} value`}
                              value={String(condition.value)}
                              onChange={(event) =>
                                updateCondition(ruleIndex, conditionIndex, {
                                  value: event.target.value === 'true',
                                })
                              }
                            >
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : (
                            <input
                              aria-label={`${rule.id} condition ${conditionIndex + 1} value`}
                              value={String(condition.value)}
                              onChange={(event) =>
                                updateCondition(ruleIndex, conditionIndex, {
                                  value: parseConditionValue(condition.field, event.target.value),
                                })
                              }
                            />
                          )}
                        </label>
                        <button
                          type="button"
                          aria-label={`Remove condition ${conditionIndex + 1} from ${rule.id}`}
                          onClick={() =>
                            updateRule(ruleIndex, {
                              conditions: rule.conditions.filter(
                                (_, itemIndex) => itemIndex !== conditionIndex,
                              ),
                            })
                          }
                          className="self-end text-red-300"
                        >
                          ×
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <button
                    type="button"
                    aria-label={`Add condition to ${rule.id}`}
                    onClick={() =>
                      updateRule(ruleIndex, {
                        conditions: [...rule.conditions, initialCondition()],
                      })
                    }
                    className="text-[0.72rem] text-teal-300"
                  >
                    + AND condition
                  </button>
                  <label className="min-w-[10rem] text-[0.68rem] text-[var(--text-tertiary)]">
                    Target
                    <select
                      aria-label={`Target for ${rule.id}`}
                      value={rule.target}
                      onChange={(event) => updateRule(ruleIndex, { target: event.target.value })}
                    >
                      {targets.map((target) => (
                        <option key={target} value={target}>
                          {target === 'skip' ? 'Skip' : target}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </li>
            ))}
            <li className="rounded-xl border border-dashed border-white/15 bg-white/2 p-3">
              <span className="text-[0.74rem] font-medium text-[var(--text-secondary)]">
                Fallback
              </span>
              <label className="mt-2 block text-[0.68rem] text-[var(--text-tertiary)]">
                Fallback target
                <select value={fallback} onChange={(event) => setFallback(event.target.value)}>
                  {targets.map((target) => (
                    <option key={target} value={target}>
                      {target === 'skip' ? 'Skip' : target}
                    </option>
                  ))}
                </select>
              </label>
            </li>
          </ol>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() =>
                setDraftRules((current) => [
                  ...current,
                  {
                    id: nextRuleId(current),
                    conditions: [initialCondition()],
                    target: 'skip',
                  },
                ])
              }
            >
              Add rule
            </button>
            <button type="button" disabled={savingRules} onClick={() => void saveRules()}>
              {savingRules ? 'Saving…' : 'Save rules'}
            </button>
          </div>

          <div className="rounded-xl border border-white/8 bg-white/3 p-3">
            <label className="block text-[0.7rem] text-[var(--text-secondary)]">
              File to test
              <input
                value={testPath}
                onChange={(event) => changeTestPath(event.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-[0.8rem] text-[var(--text-primary)]"
              />
            </label>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={testing || !testPath.trim()}
                onClick={() => void runTest()}
              >
                {testing ? 'Testing…' : 'Test file'}
              </button>
              <button
                type="button"
                disabled={reprocessing || !testPath.trim()}
                onClick={() => void reprocess()}
              >
                {reprocessing ? 'Reprocessing…' : 'Reprocess file'}
              </button>
            </div>

            {testResult && (
              <div className="mt-3 space-y-1 text-[0.72rem] text-[var(--text-secondary)]">
                <p>
                  Matched: {testResult.matched_rule ?? 'Fallback'} → {testResult.target}
                </p>
                <p>Evaluated: {testResult.evaluated.join(', ') || 'None'}</p>
                <p>Not evaluated: {testResult.not_evaluated.join(', ') || 'None'}</p>
                {Object.keys(testResult.facts).length > 0 && (
                  <pre className="overflow-x-auto rounded-lg bg-black/15 p-2 text-[0.66rem]">
                    {JSON.stringify(testResult.facts, null, 2)}
                  </pre>
                )}
              </div>
            )}
            {reprocessMessage && (
              <p role="status" className="mt-2 text-[0.72rem] text-teal-300">
                {reprocessMessage}
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
