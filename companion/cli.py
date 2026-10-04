from __future__ import annotations
import json, sys
from companion.tabby_proxy import send_command

if __name__=='__main__':
    command=sys.argv[1] if len(sys.argv)>1 else 'status'
    print(json.dumps(send_command({'command':command}),ensure_ascii=False))
