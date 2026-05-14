import React, { useEffect, useState } from 'react';
import api from '../services/api';
import ExpenseChart from '../components/ExpenseChart';
import ExpenseForm from '../components/ExpenseForm';
import ExpenseList from '../components/ExpenseList';

export default function Dashboard() {
  const [summary, setSummary] = useState({ total: 0, breakdown: [] });
  const [expenses, setExpenses] = useState([]);

  const fetchData = async () => {
    try {
      const [summaryRes, expensesRes] = await Promise.all([
        api.get('/reports/summary'),
        api.get('/expenses')
      ]);
      setSummary(summaryRes.data);
      setExpenses(expensesRes.data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  return (
    <div className="p-8">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">SpendWise Dashboard</h1>
        <button onClick={() => { localStorage.removeItem('token'); window.location.reload(); }} className="bg-red-500 text-white px-4 py-2 rounded">Logout</button>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8">
        <div className="bg-white p-6 rounded shadow">
          <h2 className="text-xl font-semibold mb-4">Monthly Summary</h2>
          <p className="text-4xl font-bold text-blue-600">${summary.total.toFixed(2)}</p>
          <div className="h-64">
            <ExpenseChart data={summary.breakdown} />
          </div>
        </div>
        <div className="bg-white p-6 rounded shadow">
          <h2 className="text-xl font-semibold mb-4">Add New Expense</h2>
          <ExpenseForm onAdd={fetchData} />
        </div>
      </div>

      <div className="bg-white p-6 rounded shadow">
        <h2 className="text-xl font-semibold mb-4">Recent Expenses</h2>
        <ExpenseList expenses={expenses} onDelete={fetchData} />
      </div>
    </div>
  );
}