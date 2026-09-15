import { createContext, useContext, useEffect, useMemo, useState } from 'react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(localStorage.getItem('agridirect_token') || '');
  const [user, setUser] = useState(() => {
    const storedUser = localStorage.getItem('agridirect_user');
    return storedUser ? JSON.parse(storedUser) : null;
  });

  useEffect(() => {
    if (token) {
      localStorage.setItem('agridirect_token', token);
    } else {
      localStorage.removeItem('agridirect_token');
    }
  }, [token]);

  useEffect(() => {
    if (user) {
      localStorage.setItem('agridirect_user', JSON.stringify(user));
    } else {
      localStorage.removeItem('agridirect_user');
    }
  }, [user]);

  const value = useMemo(
    () => ({
      token,
      user,
      setToken,
      setUser,
      logout: () => {
        setToken('');
        setUser(null);
      },
    }),
    [token, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
