import { Link, NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Navbar() {
  const { user, logout } = useAuth();

  return (
    <header className="topbar">
      <div className="brand-wrap">
        <div className="brand-mark">A</div>
        <div>
          <p className="brand-name">AgriDirect</p>
          <span className="brand-tagline">Fresh From Farm. Direct to You.</span>
        </div>
      </div>

      <nav className="main-nav" aria-label="Main navigation">
        <NavLink to="/">Home</NavLink>
        <NavLink to="/products">Products</NavLink>
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/farmers">Farmers</NavLink>
        <NavLink to="/about">About</NavLink>
        <NavLink to="/contact">Contact</NavLink>
      </nav>

      <div className="nav-actions">
        {user ? (
          <>
            <span className="user-pill">Hi, {user.name}</span>
            <Link to="/dashboard" className="button secondary">Dashboard</Link>
            <Link to="/cart" className="button secondary">Cart</Link>
            <button className="button ghost" type="button" onClick={logout}>Logout</button>
          </>
        ) : (
          <>
            <Link to="/auth" className="button ghost">Login</Link>
            <Link to="/auth" className="button primary">Register</Link>
          </>
        )}
      </div>
    </header>
  );
}
