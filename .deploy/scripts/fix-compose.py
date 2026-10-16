import yaml
with open("docker-compose.yaml", "r") as f:
    data = yaml.safe_load(f)
if "openbao" in data.get("services", {}):
    data["services"]["openbao"]["user"] = "1000:1000"
with open("docker-compose.yaml", "w") as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
print("user added")
