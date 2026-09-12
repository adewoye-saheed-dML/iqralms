import yaml
import sys

def main():
    with open('schema.yml', 'r') as f:
        schema = yaml.safe_load(f)
        
    paths = schema.get('paths', {})
    
    with open('API_INVENTORY.md', 'w') as out:
        out.write("# API Inventory\n\n")
        for path, methods in paths.items():
            for method, details in methods.items():
                out.write(f"## {method.upper()} {path}\n\n")
                out.write(f"**Description**: {details.get('summary', details.get('description', ''))}\n\n")
                out.write(f"- **Auth**: Required\n")
                out.write(f"- **Tenant Scope**: Requires investigation\n")
                out.write(f"- **Roles**: Requires investigation\n")
                
                # Parameters
                params = details.get('parameters', [])
                if params:
                    out.write(f"- **Parameters**:\n")
                    for p in params:
                        out.write(f"  - {p['name']} ({p['in']})\n")
                        
                # Request Body
                req = details.get('requestBody', {})
                if req:
                    out.write(f"- **Request Body**: Yes\n")
                    
                # Responses
                resps = details.get('responses', {})
                out.write(f"- **Status Codes**: {', '.join(resps.keys())}\n")
                
                out.write("\n")

if __name__ == '__main__':
    main()
