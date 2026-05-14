def print_status_banner(state: dict):
    print("\n" + "═" * 54)
    
    retry_count = state.get("retry_count", 0)
    if retry_count > 0:
        print(f"  🔁 RETRY LOOP (Attempt {retry_count}/3)")
        print(f"  ⚠️  QA found issues — routing back to Developer")
        print("═" * 54)
    
    status_dict = state.get("status", {})
    
    agents = [
        ("PM_agent", "PM Agent"),
        ("architecture_agent", "Architecture Agent"),
        ("developer_agent", "Developer Agent"),
        ("qa_agent", "QA Tester Agent")
    ]
    
    for agent_key, display_name in agents:
        agent_status = status_dict.get(agent_key, "PENDING")
        
        if agent_status == "COMPLETED":
            icon = "✅"
        elif agent_status == "IN_PROGRESS":
            icon = "🔄"
        else:
            icon = "⬜"
            
        print(f"  {icon} {display_name:<20} — {agent_status}")
        
    print("═" * 54 + "\n")
