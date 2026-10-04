"""Compatibility MCP shim.

Tabby is now a standalone Caelestia plugin. This module deliberately owns no
state; it forwards the legacy Protocol7 companion MCP tool names to Tabby's
private local socket so existing MCP configs do not break during the split.
"""
from __future__ import annotations
from typing import Literal
from mcp.server import MCPServer
from companion.tabby_proxy import send_command

mcp=MCPServer('hey-tabby-companion-compat')

@mcp.tool()
def companion_set_state(state: Literal['idle','wake','listening','thinking','tool','speaking','approval','success','error']) -> dict:
    return send_command({'command':'state','value':state})
@mcp.tool()
def whiteboard_show() -> dict: return send_command({'command':'show'})
@mcp.tool()
def whiteboard_hide() -> dict: return send_command({'command':'hide'})
@mcp.tool()
def whiteboard_clear() -> dict: return send_command({'command':'clear'})
@mcp.tool()
def whiteboard_write(text: str, title: str='') -> dict: return send_command({'command':'text','text':text,'title':title})
@mcp.tool()
def whiteboard_progress(value: float, label: str='') -> dict: return send_command({'command':'progress','value':value,'label':label})
@mcp.tool()
def whiteboard_choice(label: str, options: list[str]) -> dict: return send_command({'command':'choice','label':label,'options':options[:6]})
@mcp.tool()
def whiteboard_shape(kind: Literal['line','arrow','rect','circle'], x: float, y: float, w: float, h: float, label: str='') -> dict:
    return send_command({'command':'shape','kind':kind,'x':x,'y':y,'w':w,'h':h,'label':label})

if __name__=='__main__': mcp.run()
