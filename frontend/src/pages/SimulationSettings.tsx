//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Simulation Settings page: edits .jobs files with a form built from the selected Dynawo's jobs.xsd.

import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert, AutoComplete, Button, Card, Collapse, Dropdown, Empty, Form, Input, Modal,
  Popconfirm, Radio, Select, Space, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import {
  ArrowDownOutlined, ArrowUpOutlined, CloseCircleOutlined, CopyOutlined, DeleteOutlined,
  FileAddOutlined, HistoryOutlined, MinusCircleOutlined, PlusOutlined, RollbackOutlined, SaveOutlined, WarningOutlined,
} from '@ant-design/icons'
import client from '../api/client'
import { errorDetail, errorStatus } from '../api/errors'
import {
  addableChildren, canRemove, childSpec, fileRefType, getNode, insertChild, isDynElement, isElement,
  issuesAt, jobIndexes, jobNames, moveChild, parSetSource, referenceTo, removeChild, sessionPath,
  setAttribute, uniqueName,
  type AttributeSpec, type ElementNode, type ElementSpec, type Issue, type JobsDocument, type Path,
  type Schema, type TreeNode,
} from './jobsTree'

const { Title, Text } = Typography

interface SessionFile { name: string; ftype: string | null }
interface ParseError { message: string; line: number | null; column: number | null }

/** The 422 detail of an XML that does not parse, if that is what the error is. */
function parseError(err: unknown): ParseError | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return detail && typeof detail === 'object' && 'message' in detail ? detail as ParseError : null
}

/** What the form components share. */
interface Ctx {
  schema: Schema
  issues: Issue[]
  jobsFile: string
  files: SessionFile[]
  parSets: Record<string, string[]>
  loadParSets: (file: string) => void
  onAttr: (path: Path, name: string, value: string | undefined) => void
  onAdd: (path: Path, schemaPath: string, spec: ElementSpec | undefined, child: ElementSpec) => void
  onRemove: (path: Path, index: number) => void
  onMove: (path: Path, index: number, delta: -1 | 1) => void
}

function elementLabel(schema: Schema, tag: string): string {
  return schema.hints.elements[tag]?.label ?? tag
}

function AttributeField({ ctx, node, path, name, spec }: {
  ctx: Ctx; node: ElementNode; path: Path; name: string; spec: AttributeSpec | undefined
}) {
  const value = node.attrs[name]
  const hint = ctx.schema.hints.attributes[`${node.tag}@${name}`] ?? {}
  const set = (v: string | undefined) => ctx.onAttr(path, name, v)
  const ftype = fileRefType(ctx.schema.file_refs, node.tag, name)
  const parFileAttr = parSetSource(ctx.schema.file_refs, node.tag, name)
  const width = { width: 360 }

  if (ftype !== undefined) {
    const options = ctx.files
      .filter(f => ftype === null || f.ftype === ftype)
      .map(f => ({ value: referenceTo(ctx.jobsFile, f.name), label: f.name }))
    return <AutoComplete style={width} value={value} options={options} onChange={set}
      filterOption={(input, o) => (o?.label ?? '').toLowerCase().includes(input.toLowerCase())} />
  }
  if (parFileAttr && node.attrs[parFileAttr]) {
    const parFile = sessionPath(ctx.jobsFile, node.attrs[parFileAttr])
    const sets = ctx.parSets[parFile] ?? []
    return <AutoComplete style={width} value={value} onChange={set}
      onFocus={() => ctx.loadParSets(parFile)} options={sets.map(s => ({ value: s }))} />
  }
  if (spec?.type === 'enum' || spec?.type === 'boolean') {
    const values = spec.type === 'enum' ? spec.values ?? [] : ['true', 'false']
    return <Select style={{ width: 200 }} value={value} allowClear={!spec.required}
      onChange={v => set(v ?? undefined)} options={values.map(v => ({ value: v }))} />
  }
  if (hint.values) {
    return <AutoComplete style={width} value={value} onChange={set} options={hint.values.map(v => ({ value: v }))} />
  }
  return <Input style={width} value={value} onChange={e => set(e.target.value)}
    inputMode={spec?.type === 'number' || spec?.type === 'integer' ? 'decimal' : undefined}
    suffix={hint.unit ? <Text type="secondary">{hint.unit}</Text> : undefined} />
}

function Attributes({ ctx, node, path, spec }: { ctx: Ctx; node: ElementNode; path: Path; spec: ElementSpec | undefined }) {
  const specs = spec?.attributes ?? []
  const shown = [
    ...specs.filter(a => a.required || a.name in node.attrs).map(a => a.name),
    ...Object.keys(node.attrs).filter(n => !specs.some(a => a.name === n)),
  ]
  const missing = specs.filter(a => !shown.includes(a.name))
  const issues = issuesAt(ctx.issues, path)

  return (
    <Form layout="horizontal" labelCol={{ flex: '220px' }} labelWrap colon={false} style={{ marginBottom: 4 }}>
      {shown.map(name => {
        const attrSpec = specs.find(a => a.name === name)
        const hint = ctx.schema.hints.attributes[`${node.tag}@${name}`] ?? {}
        const own = issues.filter(i => i.attribute === name)
        const status = own.some(i => i.level === 'error') ? 'error' : own.length ? 'warning' : undefined
        return (
          <Form.Item key={name} style={{ marginBottom: 8 }} validateStatus={status}
            help={own.length ? own.map(i => i.message).join(' · ') : undefined}
            extra={hint.help}
            label={<Tooltip title={name}><span>{hint.label ?? name}</span></Tooltip>}>
            <Space>
              <AttributeField ctx={ctx} node={node} path={path} name={name} spec={attrSpec} />
              {!attrSpec && <Tag color="orange">unknown to this Dynawo</Tag>}
              {!attrSpec?.required && (
                <Tooltip title="Remove this attribute">
                  <Button type="text" size="small" icon={<MinusCircleOutlined />} onClick={() => ctx.onAttr(path, name, undefined)} />
                </Tooltip>
              )}
            </Space>
          </Form.Item>
        )
      })}
      {missing.length > 0 && (
        <Dropdown menu={{
          items: missing.map(a => ({ key: a.name, label: ctx.schema.hints.attributes[`${node.tag}@${a.name}`]?.label ?? a.name })),
          onClick: ({ key }) => {
            const a = missing.find(m => m.name === key)
            ctx.onAttr(path, key, ctx.schema.hints.attributes[`${node.tag}@${key}`]?.default ?? a?.default ?? '')
          },
        }}>
          <Button size="small" type="dashed" icon={<PlusOutlined />}>Attribute</Button>
        </Dropdown>
      )}
    </Form>
  )
}

function AddMenu({ ctx, node, path, schemaPath, spec }: {
  ctx: Ctx; node: ElementNode; path: Path; schemaPath: string; spec: ElementSpec | undefined
}) {
  const addable = addableChildren(node, spec)
  if (!addable.length) return null
  return (
    <Dropdown menu={{
      items: addable.map(c => ({ key: c.name, label: elementLabel(ctx.schema, c.name) })),
      onClick: ({ key }) => ctx.onAdd(path, schemaPath, spec, addable.find(c => c.name === key)!),
    }}>
      <Button size="small" type="dashed" icon={<PlusOutlined />}>Add…</Button>
    </Dropdown>
  )
}

function ElementIssues({ ctx, path }: { ctx: Ctx; path: Path }) {
  const issues = issuesAt(ctx.issues, path).filter(i => !i.attribute)
  if (!issues.length) return null
  return (
    <Space orientation="vertical" style={{ width: '100%', marginBottom: 8 }}>
      {issues.map((i, k) => <Alert key={k} type={i.level} showIcon title={i.message} />)}
    </Space>
  )
}

function ElementBody({ ctx, node, path, schemaPath, spec }: {
  ctx: Ctx; node: ElementNode; path: Path; schemaPath: string; spec: ElementSpec | undefined
}) {
  return (
    <div id={`node-${path.join('-')}`}>
      <ElementIssues ctx={ctx} path={path} />
      <Attributes ctx={ctx} node={node} path={path} spec={spec} />
      {node.children.map((child, i) => (
        <ChildElement key={i} ctx={ctx} parent={node} parentSpec={spec} path={path} index={i}
          child={child} schemaPath={schemaPath} />
      ))}
      <AddMenu ctx={ctx} node={node} path={path} schemaPath={schemaPath} spec={spec} />
    </div>
  )
}

function ChildElement({ ctx, parent, parentSpec, path, index, child, schemaPath }: {
  ctx: Ctx; parent: ElementNode; parentSpec: ElementSpec | undefined; path: Path; index: number
  child: TreeNode; schemaPath: string
}) {
  if ('comment' in child) {
    return <div style={{ margin: '4px 0' }}><Text type="secondary" italic>{`<!--${child.comment}-->`}</Text></div>
  }
  if (!isElement(child)) return null
  const spec = childSpec(parentSpec, child)
  const repeatable = spec ? spec.max !== 1 : false
  const childPath = [...path, index]
  return (
    <Card size="small" style={{ marginBottom: 8 }}
      title={<Space>
        {elementLabel(ctx.schema, child.tag)}
        <Text type="secondary" code>{child.tag}</Text>
        {!spec && <Tag color="orange">unknown to this Dynawo</Tag>}
      </Space>}
      extra={<Space size={0}>
        {repeatable && <Button type="text" size="small" icon={<ArrowUpOutlined />} onClick={() => ctx.onMove(path, index, -1)} />}
        {repeatable && <Button type="text" size="small" icon={<ArrowDownOutlined />} onClick={() => ctx.onMove(path, index, 1)} />}
        {canRemove(parent, parentSpec, child.tag) && (
          <Tooltip title="Remove"><Button type="text" size="small" danger icon={<DeleteOutlined />}
            onClick={() => ctx.onRemove(path, index)} /></Tooltip>
        )}
      </Space>}>
      <ElementBody ctx={ctx} node={child} path={childPath} schemaPath={`${schemaPath}/${child.tag}`} spec={spec} />
    </Card>
  )
}

function issueLocation(schema: Schema, root: ElementNode, path: Path): string {
  const labels: string[] = []
  let node = root
  for (const i of path) {
    node = node.children[i] as ElementNode
    if (!node) break
    labels.push(node.tag === 'job' ? `job ${node.attrs.name ?? ''}` : elementLabel(schema, node.tag))
  }
  return labels.join(' › ')
}

export default function SimulationSettings() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [schema, setSchema] = useState<Schema | null>(null)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [files, setFiles] = useState<SessionFile[]>([])
  const [jobsFile, setJobsFile] = useState<string | null>(null)
  const [doc, setDoc] = useState<JobsDocument | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [issues, setIssues] = useState<Issue[]>([])
  const [jobIndex, setJobIndex] = useState<number | null>(null)
  const [openPanels, setOpenPanels] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [modified, setModified] = useState(false)  // file on disk differs from its original
  const [tab, setTab] = useState<'form' | 'xml'>('form')
  const [xmlText, setXmlText] = useState('')
  const [xmlDirty, setXmlDirty] = useState(false)
  const [xmlError, setXmlError] = useState<ParseError | null>(null)
  const [parSets, setParSets] = useState<Record<string, string[]>>({})
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [newFileOpen, setNewFileOpen] = useState(false)
  const [newFileName, setNewFileName] = useState('')
  const [newFileSource, setNewFileSource] = useState<'template' | 'copy'>('template')
  const checkVersion = useRef(0)

  const jobsFiles = files.filter(f => f.ftype === 'jobs').map(f => f.name)

  const fetchFiles = async () => {
    const res = await client.get<SessionFile[]>('/files/')
    setFiles(res.data)
    return res.data
  }

  const loadFile = async (name: string) => {
    try {
      const res = await client.get<{
        document: JobsDocument | null; token: string; modified: boolean; text?: string; error?: ParseError
      }>(`/jobs/file/${encodeURIComponent(name)}`)
      setError(null); setSuccess(null); setConflict(null); setXmlError(null)
      setToken(res.data.token)
      setModified(res.data.modified)
      setDirty(false)
      setXmlDirty(false)
      if (!res.data.document) {
        // The file on disk does not parse: open it as text so it can be fixed.
        setDoc(null)
        setIssues([])
        setXmlText(res.data.text ?? '')
        setXmlError(res.data.error ?? null)
        setTab('xml')
        return
      }
      const checked = await client.post<{ document: JobsDocument; issues: Issue[] }>(
        '/jobs/check', { document: res.data.document, jobs_file: name })
      setDoc(checked.data.document)
      setIssues(checked.data.issues)
      setJobIndex(jobIndexes(checked.data.document.root)[0] ?? null)
      setTab('form')
    } catch (err) {
      setDoc(null)
      setError(errorDetail(err, `Could not read ${name}`))
    }
  }

  const openFile = (name: string) => {
    setJobsFile(name)
    loadFile(name)
  }

  // Schema first: without it nothing can be shown.
  useEffect(() => {
    client.get<Schema>('/jobs/schema')
      .then(async res => {
        setSchema(res.data)
        const list = (await client.get<SessionFile[]>('/files/')).data
        setFiles(list)
        const names = list.filter(f => f.ftype === 'jobs').map(f => f.name)
        const wanted = searchParams.get('jobs')
        const first = wanted && names.includes(wanted) ? wanted : names[0]
        if (first) openFile(first)
      })
      .catch(err => setSchemaError(errorDetail(err, 'Could not load the jobs schema')))
  }, [])

  // Live check of the edited document; results of an older version are dropped.
  useEffect(() => {
    if (!dirty || !doc || !jobsFile || tab !== 'form') return
    const version = ++checkVersion.current
    const timer = setTimeout(async () => {
      try {
        const res = await client.post<{ issues: Issue[] }>('/jobs/check', { document: doc, jobs_file: jobsFile })
        if (version === checkVersion.current) setIssues(res.data.issues)
      } catch { /* the next edit checks again */ }
    }, 400)
    return () => clearTimeout(timer)
  }, [doc, dirty, jobsFile, tab])

  const edit = (fn: (root: ElementNode) => ElementNode) => {
    setDoc(d => (d ? { ...d, root: fn(d.root) } : d))
    setDirty(true)
    setSuccess(null)
  }

  const loadParSets = (file: string) => {
    if (parSets[file] || !files.some(f => f.name === file && f.ftype === 'par')) return
    client.get<string[]>(`/jobs/par-sets/${encodeURIComponent(file)}`)
      .then(res => setParSets(p => ({ ...p, [file]: res.data })))
      .catch(() => setParSets(p => ({ ...p, [file]: [] })))
  }

  const newNode = async (schemaPath: string) =>
    (await client.get<{ node: ElementNode }>('/jobs/new', { params: { path: schemaPath } })).data.node

  const confirmDiscard = (then: () => void) => {
    if (!dirty) return then()
    Modal.confirm({ title: 'Discard the unsaved changes?', okText: 'Discard', okButtonProps: { danger: true }, onOk: then })
  }

  // ── XML tab ────────────────────────────────────────────────────────────────

  /** Parse the XML text into the document; false if it does not parse. */
  const applyXml = async (): Promise<JobsDocument | null | false> => {
    if (!xmlDirty) return doc
    try {
      const res = await client.post<{ document: JobsDocument; issues: Issue[] }>(
        '/jobs/parse', { text: xmlText, jobs_file: jobsFile })
      setDoc(res.data.document)
      setIssues(res.data.issues)
      setXmlDirty(false)
      setXmlError(null)
      const jobs = jobIndexes(res.data.document.root)
      if (jobIndex === null || !jobs.includes(jobIndex)) setJobIndex(jobs[0] ?? null)
      return res.data.document
    } catch (err) {
      setXmlError(parseError(err) ?? { message: errorDetail(err, 'Invalid XML'), line: null, column: null })
      return false
    }
  }

  const changeTab = async (key: string) => {
    if (key === 'xml' && doc) {
      try {
        const res = await client.post<{ text: string }>('/jobs/render', { document: doc })
        setXmlText(res.data.text)
        setXmlDirty(false)
        setXmlError(null)
      } catch (err) {
        setError(errorDetail(err, 'Could not show the XML'))
        return
      }
      setTab('xml')
    } else if (key === 'form') {
      if ((await applyXml()) !== false) setTab('form')
    }
  }

  // ── Saving ─────────────────────────────────────────────────────────────────

  const doSave = async (document: JobsDocument) => {
    if (!jobsFile || !token) return
    setSaving(true); setError(null); setSuccess(null); setConflict(null)
    try {
      const res = await client.put<{ document: JobsDocument; token: string; issues: Issue[]; modified: boolean }>(
        '/jobs/file', { jobs_file: jobsFile, document, token })
      setDoc(res.data.document)
      setToken(res.data.token)
      setModified(res.data.modified)
      setIssues(res.data.issues)
      setDirty(false)
      if (tab === 'xml') {
        const rendered = await client.post<{ text: string }>('/jobs/render', { document: res.data.document })
        setXmlText(rendered.data.text)
      }
      setSuccess(`${jobsFile} saved.`)
    } catch (err) {
      if (errorStatus(err) === 409) setConflict(errorDetail(err, 'The file was changed since it was read'))
      else setError(errorDetail(err, 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  const handleSave = async () => {
    const document = tab === 'xml' ? await applyXml() : doc
    if (!document) return
    // Issues of the document as it will be saved.
    const res = await client.post<{ issues: Issue[] }>('/jobs/check', { document, jobs_file: jobsFile })
    setIssues(res.data.issues)
    const errors = res.data.issues.filter(i => i.level === 'error').length
    if (!errors) return doSave(document)
    Modal.confirm({
      title: `${errors} error${errors > 1 ? 's' : ''} against the Dynawo schema`,
      content: 'Dynawo will most likely refuse this file. Save anyway?',
      okText: 'Save anyway',
      onOk: () => doSave(document),
    })
  }

  const handleRestore = async () => {
    if (!jobsFile) return
    try {
      await client.post(`/jobs/restore/${encodeURIComponent(jobsFile)}`)
      await loadFile(jobsFile)
      setSuccess(`${jobsFile} restored to its original content.`)
    } catch (err) {
      setError(errorDetail(err, 'Restore failed'))
    }
  }

  // ── Jobs and files ─────────────────────────────────────────────────────────

  const addJob = async (copyOf?: ElementNode) => {
    if (!doc || !schema) return
    const node = copyOf ? structuredClone(copyOf) : await newNode('jobs/job')
    const base = copyOf ? `${copyOf.attrs.name ?? 'job'}_copy` : 'job'
    node.attrs = { ...node.attrs, name: uniqueName(base, jobNames(doc.root)) }
    const { root, index } = insertChild(doc.root, [], schema.root, node)
    edit(() => root)
    setJobIndex(index)
  }

  const removeJob = () => {
    if (!doc || jobIndex === null) return
    const root = removeChild(doc.root, [], jobIndex)
    edit(() => root)
    setJobIndex(jobIndexes(root)[0] ?? null)
  }

  const openNewFile = () => {
    setNewFileName(uniqueName('new', jobsFiles.map(f => f.replace(/\.jobs$/, ''))) + '.jobs')
    setNewFileSource(doc && jobIndex !== null ? 'copy' : 'template')
    setNewFileOpen(true)
  }

  const createFile = async () => {
    if (!schema) return
    const name = newFileName.trim()
    try {
      const root = await newNode('jobs')
      if (newFileSource === 'copy' && doc && jobIndex !== null) {
        root.children = [structuredClone(getNode(doc.root, [jobIndex]))]
      }
      await client.post('/jobs/file', { jobs_file: name, document: { root } })
      await fetchFiles()
      setNewFileOpen(false)
      confirmDiscard(() => openFile(name))
    } catch (err) {
      Modal.error({ title: 'Could not create the file', content: errorDetail(err, 'Error') })
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (schemaError) {
    return (
      <div style={{ maxWidth: 1000 }}>
        <Title level={3}>Simulation Settings <Text type="secondary">(.jobs file)</Text></Title>
        <Alert type="warning" showIcon
          description={<>{schemaError} <a onClick={() => navigate('/dynawo-version')}>Dynawo Version</a></>} />
      </div>
    )
  }

  const job = doc && jobIndex !== null && doc.root.children[jobIndex] ? getNode(doc.root, [jobIndex]) : null
  const jobSpec = schema?.root.children.find(c => c.name === 'job')
  const errors = issues.filter(i => i.level === 'error').length
  const warnings = issues.length - errors

  const ctx: Ctx | null = schema && jobsFile ? {
    schema, issues, jobsFile, files, parSets, loadParSets,
    onAttr: (path, name, value) => edit(root => setAttribute(root, path, name, value)),
    onAdd: async (path, schemaPath, spec, child) => {
      const node = await newNode(`${schemaPath}/${child.name}`)
      edit(root => insertChild(root, path, spec, node).root)
    },
    onRemove: (path, index) => edit(root => removeChild(root, path, index)),
    onMove: (path, index, delta) => edit(root => moveChild(root, path, index, delta)),
  } : null

  const goToIssue = (issue: Issue) => {
    if (!doc) return
    const [first, second] = issue.path
    if (first !== undefined && jobIndexes(doc.root).includes(first)) setJobIndex(first)
    if (second !== undefined) setOpenPanels(p => (p.includes(String(second)) ? p : [...p, String(second)]))
    setTab('form')
    setTimeout(() => {
      for (let n = issue.path.length; n >= 0; n--) {
        const el = document.getElementById(`node-${issue.path.slice(0, n).join('-')}`)
        if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); break }
      }
    }, 100)
  }

  const formTab = ctx && doc && job ? (
    <Space orientation="vertical" style={{ width: '100%' }} size={12}>
      <Card size="small" title={<Space>{elementLabel(ctx.schema, 'job')}<Text type="secondary" code>job</Text></Space>}>
        <div id={`node-${jobIndex}`}>
          <ElementIssues ctx={ctx} path={[jobIndex!]} />
          <Attributes ctx={ctx} node={job} path={[jobIndex!]} spec={jobSpec} />
        </div>
      </Card>
      <Collapse activeKey={openPanels} onChange={keys => setOpenPanels(keys as string[])}
        items={job.children.flatMap((child, i) => {
          if (!isElement(child)) return []
          const spec = childSpec(jobSpec, child)
          const path = [jobIndex!, i]
          const own = issues.filter(x => x.path.length >= 2 && x.path[0] === jobIndex && x.path[1] === i)
          return [{
            key: String(i),
            label: <Space>
              {elementLabel(ctx.schema, child.tag)}
              <Text type="secondary" code>{child.tag}</Text>
              {!isDynElement(child) || !spec ? <Tag color="orange">unknown to this Dynawo</Tag> : null}
              {own.some(x => x.level === 'error') && <CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
              {!own.some(x => x.level === 'error') && own.length > 0 && <WarningOutlined style={{ color: '#faad14' }} />}
            </Space>,
            extra: canRemove(job, jobSpec, child.tag) ? (
              <Button type="text" size="small" danger icon={<DeleteOutlined />}
                onClick={e => { e.stopPropagation(); ctx.onRemove([jobIndex!], i) }} />
            ) : undefined,
            children: <ElementBody ctx={ctx} node={child} path={path} schemaPath={`jobs/job/${child.tag}`} spec={spec} />,
          }]
        })} />
      <AddMenu ctx={ctx} node={job} path={[jobIndex!]} schemaPath="jobs/job" spec={jobSpec} />
    </Space>
  ) : doc ? <Empty description="No job in this file" /> : null

  const xmlTab = (
    <Space orientation="vertical" style={{ width: '100%' }}>
      {xmlError && (
        <Alert type="error" showIcon
          title={`The XML does not parse${xmlError.line ? ` (line ${xmlError.line}${xmlError.column ? `, column ${xmlError.column}` : ''})` : ''}`}
          description={xmlError.message} />
      )}
      <Input.TextArea value={xmlText} autoSize={{ minRows: 20 }} spellCheck={false}
        style={{ fontFamily: 'monospace', fontSize: 13 }}
        onChange={e => { setXmlText(e.target.value); setXmlDirty(true); setDirty(true); setSuccess(null) }} />
    </Space>
  )

  return (
    <div style={{ maxWidth: 1100 }}>
      <Title level={3}>Simulation Settings <Text type="secondary">(.jobs file)</Text></Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Text>File</Text>
          <Select style={{ width: 260 }} value={jobsFile ?? undefined} placeholder="no .jobs file in the session"
            options={jobsFiles.map(f => ({ value: f }))}
            onChange={v => confirmDiscard(() => openFile(v))} />
          <Button icon={<FileAddOutlined />} onClick={openNewFile} disabled={!schema}>New file</Button>
          {doc && (
            <>
              <Text style={{ marginLeft: 16 }}>Job</Text>
              <Select style={{ width: 200 }} value={jobIndex ?? undefined}
                options={jobIndexes(doc.root).map(i => ({ value: i, label: getNode(doc.root, [i]).attrs.name || '(no name)' }))}
                onChange={v => setJobIndex(v)} disabled={tab === 'xml'} />
              <Button icon={<PlusOutlined />} onClick={() => addJob()} disabled={tab === 'xml'}>Add job</Button>
              <Button icon={<CopyOutlined />} onClick={() => job && addJob(job)} disabled={!job || tab === 'xml'}>Duplicate</Button>
              <Popconfirm title="Remove this job?" okText="Remove" okButtonProps={{ danger: true }} onConfirm={removeJob}
                disabled={jobIndexes(doc.root).length < 2 || tab === 'xml'}>
                <Button danger icon={<DeleteOutlined />} disabled={jobIndexes(doc.root).length < 2 || tab === 'xml'}>Remove</Button>
              </Popconfirm>
            </>
          )}
        </Space>
        {schema && (
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>Checked against {schema.xsd}</Text>
          </div>
        )}
      </Card>

      {!jobsFiles.length && !doc && !xmlText && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
          description="No .jobs file in the session: upload one, or create one with New file." />
      )}
      {error && <Alert type="error" showIcon closable style={{ marginBottom: 16 }} description={error} onClose={() => setError(null)} />}
      {success && <Alert type="success" showIcon closable style={{ marginBottom: 16 }} description={success} onClose={() => setSuccess(null)} />}
      {conflict && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          title="The file was changed since it was opened (by another page or a simulation run)"
          description={conflict}
          action={<Button size="small" onClick={() => jobsFile && loadFile(jobsFile)}>Reload, losing my changes</Button>} />
      )}

      {(doc || xmlText) && (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Tabs activeKey={tab} onChange={changeTab}
              tabBarExtraContent={<Space>
                {dirty && <Tag color="blue">unsaved</Tag>}
                <Tooltip title="Drop the changes not saved yet">
                  <Button icon={<RollbackOutlined />} disabled={!dirty} onClick={() => jobsFile && loadFile(jobsFile)}>Discard</Button>
                </Tooltip>
                <Popconfirm title="Restore the original file?"
                  description="The file goes back to its content when it entered the session; all saved changes are lost."
                  okText="Restore" okButtonProps={{ danger: true }} onConfirm={handleRestore} disabled={!modified}>
                  <Button icon={<HistoryOutlined />} disabled={!modified}>Restore original</Button>
                </Popconfirm>
                <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={handleSave}>Save</Button>
              </Space>}
              items={[
                { key: 'form', label: 'Form', children: formTab, disabled: !doc && !xmlDirty },
                { key: 'xml', label: 'XML', children: xmlTab },
              ]} />
          </div>
          <Card size="small" style={{ width: 320, flexShrink: 0, position: 'sticky', top: 0 }}
            title={<Space>Problems
              {errors > 0 && <Tag color="red">{errors} error{errors > 1 ? 's' : ''}</Tag>}
              {warnings > 0 && <Tag color="orange">{warnings} warning{warnings > 1 ? 's' : ''}</Tag>}
            </Space>}>
            {issues.length === 0 ? <Text type="secondary">None</Text> : (
              <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
                {issues.map((issue, k) => (
                  <div key={k} onClick={() => goToIssue(issue)}
                    style={{ cursor: 'pointer', padding: '6px 0', borderTop: k ? '1px solid rgba(128,128,128,0.15)' : undefined }}>
                    <Space align="start">
                      {issue.level === 'error'
                        ? <CloseCircleOutlined style={{ color: '#ff4d4f', marginTop: 4 }} />
                        : <WarningOutlined style={{ color: '#faad14', marginTop: 4 }} />}
                      <div>
                        <div>{issue.message}</div>
                        {doc && schema && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {issueLocation(schema, doc.root, issue.path)}{issue.attribute ? ` › ${issue.attribute}` : ''}
                          </Text>
                        )}
                      </div>
                    </Space>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}

      <Modal open={newFileOpen} title="New jobs file" okText="Create" onOk={createFile}
        onCancel={() => setNewFileOpen(false)} okButtonProps={{ disabled: !newFileName.trim().endsWith('.jobs') }}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Text>File name (may include a folder, e.g. <Text code>cases/new.jobs</Text>)</Text>
          <Input value={newFileName} onChange={e => setNewFileName(e.target.value)} />
          <Radio.Group value={newFileSource} onChange={e => setNewFileSource(e.target.value)}>
            <Space orientation="vertical">
              <Radio value="template">Start from a template (required settings and common defaults)</Radio>
              <Radio value="copy" disabled={!job}>
                Copy the current job{job ? ` (${job.attrs.name ?? ''} of ${jobsFile})` : ''}
              </Radio>
            </Space>
          </Radio.Group>
        </Space>
      </Modal>
    </div>
  )
}
