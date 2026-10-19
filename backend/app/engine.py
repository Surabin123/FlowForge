import asyncio, json, uuid
from datetime import timedelta
from .database import connect, now
from .config import settings

class WorkflowError(ValueError): pass

def validate_output_schemas(nodes):
    from jsonschema import Draft202012Validator, SchemaError
    for node in nodes:
        schema=node.get("output_schema")
        if schema:
            try: Draft202012Validator.check_schema(schema)
            except SchemaError as exc: raise WorkflowError(f"Invalid output schema for node {node.get('id')}: {exc.message}") from exc

def validate_dag(nodes, edges):
    ids=[str(n.get("id", "")) for n in nodes]
    if not all(ids) or len(set(ids)) != len(ids): raise WorkflowError("Every node needs a unique id")
    known=set(ids); incoming={i:0 for i in ids}; children={i:[] for i in ids}
    for e in edges:
        a,b=str(e.get("source", "")),str(e.get("target", ""))
        if a not in known or b not in known: raise WorkflowError("Every edge must refer to an existing node")
        if a == b: raise WorkflowError("A node cannot depend on itself")
        if b not in children[a]: children[a].append(b); incoming[b]+=1
    ready=[i for i,n in incoming.items() if n == 0]; order=[]
    while ready:
        i=ready.pop(0); order.append(i)
        for j in children[i]:
            incoming[j]-=1
            if incoming[j] == 0: ready.append(j)
    if len(order) != len(ids): raise WorkflowError("Workflow dependencies contain a cycle")
    return order

async def _perform(node, inputs):
    kind=node.get("type", "transform"); cfg=node.get("config") or {}
    if kind == "manual": return {"approved": True, "message": cfg.get("message", "Approved by the local operator")}
    if kind == "transform": return cfg.get("output", inputs)
    if kind == "agent":
        if not settings.ai_api_key: raise RuntimeError("AI provider is not configured. Add FLOWFORGE_AI_API_KEY and a model setting.")
        import httpx
        model=settings.ai_model; instructions=str(cfg.get("prompt", "Process the provided input.")); output_schema=node.get("output_schema") or {}; input_schema={}
        if cfg.get("agent_id"):
            with connect() as db: agent=db.execute("SELECT * FROM agents WHERE id=?",(cfg["agent_id"],)).fetchone()
            if not agent: raise RuntimeError("Selected agent no longer exists")
            instructions=agent["instructions"] or agent["purpose"] or instructions
            model=agent["model"] or model
            input_schema=json.loads(agent["input_schema"])
            output_schema=output_schema or json.loads(agent["output_schema"])
        if not model: raise RuntimeError("AI model is not configured. Add FLOWFORGE_AI_MODEL or set a model on the selected agent.")
        if input_schema:
            from jsonschema import validate
            validate(inputs,input_schema)
        prompt=instructions + "\nReturn only JSON when a schema is provided. Output schema: " + json.dumps(output_schema) + "\nInput: " + json.dumps(inputs, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=45) as client:
            res=await client.post(settings.ai_base_url.rstrip("/")+"/chat/completions",headers={"Authorization":"Bearer "+settings.ai_api_key},json={"model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.2})
            res.raise_for_status(); data=res.json()
        text=data["choices"][0]["message"]["content"]
        if output_schema:
            try: output=json.loads(text)
            except json.JSONDecodeError as exc: raise RuntimeError("AI response was not valid JSON for the configured output schema") from exc
            return output
        return {"text":text,"usage":data.get("usage",{})}
    if kind == "http": raise RuntimeError("HTTP nodes are disabled until an allowlist can be configured")
    raise RuntimeError(f"Unsupported node type: {kind}")

async def execute_run(run_id, workflow_id, workflow_name, definition):
    nodes=definition["nodes"]; edges=definition.get("edges",[]); order=validate_dag(nodes,edges)
    node_map={str(n["id"]):n for n in nodes}; outputs={}; parent={str(n["id"]):[] for n in nodes}
    for edge in edges: parent[str(edge["target"])].append(str(edge["source"]))
    with connect() as db:
        for n in nodes: db.execute("INSERT OR IGNORE INTO node_runs(id,run_id,node_id,node_name,status) VALUES(?,?,?,?,?)",(str(uuid.uuid4()),run_id,str(n["id"]),str(n.get("name",n["id"])),"queued"))
        prior=db.execute("SELECT node_id,status,output FROM node_runs WHERE run_id=?",(run_id,)).fetchall()
        states={r["node_id"]:r["status"] for r in prior}
        for r in prior:
            if r["status"]=="completed" and r["output"] is not None: outputs[r["node_id"]]=json.loads(r["output"])
    pending={nid for nid in order if states.get(nid)!="completed"}; failed=False; paused=False; log=[]
    try:
        while pending:
            ready=[nid for nid in order if nid in pending and all(p in outputs for p in parent[nid])]
            if not ready:
                failed=True; log.append("No runnable nodes remain; dependency outputs are incomplete"); break
            batch=ready[:max(1,settings.max_parallel_nodes)]
            async def run_node(nid):
                node=node_map[nid]; inputs={p:outputs[p] for p in parent[nid]}
                with connect() as db: db.execute("UPDATE node_runs SET status='running',started_at=?,attempts=attempts+1 WHERE run_id=? AND node_id=?",(now(),run_id,nid))
                try:
                    if node.get("type")=="manual":
                        with connect() as db: db.execute("UPDATE node_runs SET status='paused',finished_at=NULL WHERE run_id=? AND node_id=?",(run_id,nid))
                        return nid,None,None,True
                    result=await asyncio.wait_for(_perform(node,inputs),timeout=max(1,min(int(node.get("timeout_seconds",30)),120)))
                    schema=node.get("output_schema")
                    if schema:
                        from jsonschema import validate
                        validate(result,schema)
                    with connect() as db: db.execute("UPDATE node_runs SET status='completed',finished_at=?,output=? WHERE run_id=? AND node_id=?",(now(),json.dumps(result,ensure_ascii=False),run_id,nid))
                    return nid,result,None,False
                except Exception as exc:
                    with connect() as db: db.execute("UPDATE node_runs SET status='failed',finished_at=?,error=?,logs=? WHERE run_id=? AND node_id=?",(now(),str(exc),json.dumps([str(exc)]),run_id,nid))
                    return nid,None,str(exc),False
            results=await asyncio.gather(*(run_node(n) for n in batch))
            pending.difference_update(batch)
            for nid,value,error,needs_approval in results:
                if needs_approval:
                    paused=True; log.append(f"{node_map[nid].get('name',nid)}: waiting for approval"); continue
                if error: failed=True; log.append(f"{node_map[nid].get('name',nid)}: {error}")
                else: outputs[nid]=value; log.append(f"{node_map[nid].get('name',nid)}: completed")
            if failed or paused:
                with connect() as db: db.execute("UPDATE node_runs SET status='skipped' WHERE run_id=? AND status='queued'",(run_id,))
                break
        status="failed" if failed else "paused" if paused else "completed"
        with connect() as db: db.execute("UPDATE runs SET status=?,finished_at=?,logs=?,error=? WHERE id=?",(status,None if paused else now(),json.dumps(log),"; ".join(log) if failed else None,run_id))
    except Exception as exc:
        with connect() as db: db.execute("UPDATE runs SET status='failed',finished_at=?,error=?,logs=? WHERE id=?",(now(),str(exc),json.dumps(log+[str(exc)]),run_id))
    return {"id":run_id,"status":"failed" if failed else "paused" if paused else "completed","logs":log}
