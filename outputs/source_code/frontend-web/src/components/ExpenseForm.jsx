import React, { useState } from 'react';
import api from '../services/api';

export default function ExpenseForm({ onAdd }) {
  const [amount, setAmount] = useState('');
  const [categoryId, setCategoryId] = useState(1);
  const [description, setDescription] = useState('');
  const [date, setDate] = useState(new Date().toISOString().split('T')[0]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post('/expenses', {
        amount: parseFloat(amount),
        category_id: parseInt(categoryId),
        description,
        transaction_date: date
      });
      setAmount('');
      setDescription('');
      onAdd();
    } catch (err) {
      alert('Failed to add expense');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <input type="number" step="0.01" placeholder="Amount" className="w-full p-2 border rounded" value={amount} onChange={e => setAmount(e.target.value)} required />
      <select className="w-full p-2 border rounded" value={categoryId} onChange={e => setCategoryId(e.target.value)}>
        <option value="1">Food</option>
        <option value="2">Transport</option>
        <option value="3">Rent</option>
        <option value="4">Utilities</option>
        <option value="5">Entertainment</option>
      </select>
      <input type="text" placeholder="Description" className="w-full p-2 border rounded" value={description} onChange={e => setDescription(e.target.value)} />
      <input type="date" className="w-full p-2 border rounded" value={date} onChange={e => setDate(e.target.value)} required />
      <button type="submit" className="w-full bg-blue-600 text-white p-2 rounded">Add Expense</button>
    </form>
  );
}