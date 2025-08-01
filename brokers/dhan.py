import requests
from .base import BrokerBase
from .symbol_map import get_symbol_for_broker
import json # Import the json module

class DhanBroker(BrokerBase):
    BROKER = "dhan"
    NSE = "NSE_EQ"
    INTRA = "INTRADAY"
    BUY = "BUY"
    SELL = "SELL"
    MARKET = "MARKET"
    LIMIT = "LIMIT"

    def __init__(self, client_id, access_token, **kwargs):
        timeout = kwargs.pop("timeout", 10) # Default timeout to 10 seconds
        super().__init__(client_id, access_token, timeout=timeout, **kwargs)
        self.api_base = "https://api.dhan.co/v2"
        # It's generally not recommended to modify urllib3 internals globally.
        # If IPv6 is causing issues, it's better to configure requests specifically or address the network setup.
        # Keeping this line for now as it was in the original, but be aware of its implications.
        requests.packages.urllib3.util.connection.HAS_IPV6 = False
        self.headers = {
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Initialize symbol_map if it's expected to be used
        self.symbol_map = kwargs.pop("symbol_map", {}) # Add this line to initialize symbol_map

    def _normalize_segment(self, seg):
        """Return API-compatible exchange segment string."""
        if not seg or str(seg).upper() in ("ALL", "", "NONE"):
            return self.NSE
        seg = str(seg).upper()
        if seg == "NSE":
            return "NSE_EQ"
        if seg == "BSE":
            return "BSE_EQ"
        return seg

    # Assuming _request method exists in BrokerBase.
    # If not, you'd need to define it here or in BrokerBase.
    # Example minimal _request for demonstration if it's missing:
    # def _request(self, method, url, **kwargs):
    #     try:
    #         response = requests.request(method, url, timeout=self.timeout, **kwargs)
    #         response.raise_for_status() # Raise an exception for HTTP errors
    #         return response
    #     except requests.exceptions.Timeout as e:
    #         raise requests.exceptions.Timeout(f"Request timed out: {e}") from e
    #     except requests.exceptions.RequestException as e:
    #         raise Exception(f"Request failed: {e}") from e


    def place_order(
        self,
        tradingsymbol=None,
        security_id=None,
        exchange_segment=None,
        transaction_type=None,
        quantity=None,
        order_type="MARKET",
        product_type="INTRADAY",
        price=0,
        **extra
    ):
        """
        Place a new order on Dhan.
        """
        mapping = get_symbol_for_broker(tradingsymbol or "", self.BROKER)
        if not security_id:
            if tradingsymbol and self.symbol_map:
                security_id = self.symbol_map.get(tradingsymbol.upper())
            if not security_id:
                security_id = mapping.get("security_id")
            if not security_id:
                raise ValueError("DhanBroker: 'security_id' required (tradingsymbol={})".format(tradingsymbol)) # Use ValueError for missing mandatory data

        if not exchange_segment:
            exchange_segment = mapping.get("exchange_segment", self.NSE)

        if not product_type:
            product_type = self.INTRA

        payload = {
            "dhanClientId": self.client_id,
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "transactionType": transaction_type,
            "productType": product_type,
            "orderType": order_type,
            "quantity": int(quantity),
            "validity": "DAY",
            "price": float(price) if price else 0,
            "triggerPrice": "", # Ensure this is compatible with Dhan API requirements for different order types
            "afterMarketOrder": False,
        }

        # Handle specific order types (LIMIT orders require a price)
        if order_type == self.LIMIT and not price:
            raise ValueError("Limit orders require a 'price'.")
        if order_type == self.MARKET:
            payload.pop("price", None) # Market orders typically don't send a price
            payload.pop("triggerPrice", None) # And no trigger price
        try:
            r = self._request(
                "post",
                "{}/orders".format(self.api_base),
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
            resp = r.json()
        except requests.exceptions.Timeout:
            return {"status": "failure", "error": "Request to Dhan API timed out while placing order."}
        except requests.exceptions.RequestException as e:
            return {"status": "failure", "error": "Failed to place order with Dhan API: {}".format(str(e))}
        except json.JSONDecodeError:
            return {"status": "failure", "error": "Invalid JSON response from Dhan API: {}".format(r.text)}
        except Exception as e:
            return {"status": "failure", "error": "An unexpected error occurred while placing order: {}".format(str(e))}

        if "orderId" in resp:
            result = {"status": "success"}
            if isinstance(resp, dict):
                result.update(resp)
            return result
        result = {"status": "failure"}
        if isinstance(resp, dict):
            result.update(resp)
        return result

    def get_order_list(self, use_pagination=True, batch_size=100, max_batches=50):
        """
        Fetch order list. For large accounts, use pagination to avoid timeouts.
        If the Dhan API doesn't support offset/limit, set use_pagination=False.
        """
        if not use_pagination:
            try:
                r = self._request(
                    "get",
                    "{}/orders".format(self.api_base),
                    headers=self.headers,
                    timeout=self.timeout, # Pass timeout to the request
                )
                return {"status": "success", "data": r.json()}
            except requests.exceptions.Timeout:
                return {"status": "failure", "error": "Request to Dhan API timed out while fetching order list."}
            except requests.exceptions.RequestException as e:
                return {"status": "failure", "error": "Failed to fetch order list from Dhan API: {}".format(str(e))}
            except json.JSONDecodeError:
                return {"status": "failure", "error": "Invalid JSON response from Dhan API: {}".format(r.text)}
            except Exception as e:
                return {"status": "failure", "error": "An unexpected error occurred while fetching order list: {}".format(str(e))}

        # Paginated fetch for large accounts
        all_orders = []
        offset = 0
        for i in range(max_batches): # Use i if you want to log batch number
            try:
                url = "{}/orders?offset={}&limit={}".format(self.api_base, offset, batch_size)
                r = self._request("get", url, headers=self.headers, timeout=self.timeout) # Pass timeout
                batch = r.json()
                batch_orders = batch.get("data", batch) if isinstance(batch, dict) else batch
                if not batch_orders or len(batch_orders) == 0:
                    break
                all_orders.extend(batch_orders)
                if len(batch_orders) < batch_size:
                    break
                offset += batch_size
            except requests.exceptions.Timeout:
                # Return partial results if timeout occurs
                return {
                    "status": "partial_failure" if all_orders else "failure",
                    "error": "Request to Dhan API timed out during pagination.",
                    "data": all_orders,
                }
            except requests.exceptions.RequestException as e:
                # Return partial results if other request error occurs
                return {
                    "status": "partial_failure" if all_orders else "failure",
                    "error": "Failed to fetch order list from Dhan API during pagination: {}".format(str(e)),
                    "data": all_orders,
                }
            except json.JSONDecodeError:
                return {
                    "status": "partial_failure" if all_orders else "failure",
                    "error": "Invalid JSON response from Dhan API during pagination: {}".format(r.text),
                    "data": all_orders,
                }
            except Exception as e:
                # Return partial results if unexpected error occurs
                return {
                    "status": "partial_failure" if all_orders else "failure",
                    "error": "An unexpected error occurred during pagination: {}".format(str(e)),
                    "data": all_orders,
                }
        return {"status": "success", "data": all_orders}

    def cancel_order(self, order_id):
        """
        Cancel an order by order_id.
        """
        try:
            r = self._request(
                "delete",
                "{}/orders/{}".format(self.api_base, order_id),
                headers=self.headers,
                timeout=self.timeout, # Pass timeout
            )
            return {"status": "success", "data": r.json()}
        except requests.exceptions.Timeout:
            return {"status": "failure", "error": "Request to Dhan API timed out while canceling order."}
        except requests.exceptions.RequestException as e:
            return {"status": "failure", "error": "Failed to cancel order with Dhan API: {}".format(str(e))}
        except json.JSONDecodeError:
            return {"status": "failure", "error": "Invalid JSON response from Dhan API: {}".format(r.text)}
        except Exception as e:
            return {"status": "failure", "error": "An unexpected error occurred while canceling order: {}".format(str(e))}

    def get_positions(self):
        """Fetch current positions and enrich them with LTP and P/L."""
        try:
            r = self._request(
                "get",
                f"{self.api_base}/positions",
                headers=self.headers,
                timeout=self.timeout,
            )
            data = r.json()
            if isinstance(data, dict):
                positions = (
                    data.get("data")
                    or data.get("positions")
                    or data.get("net")
                    or data.get("netPositions")
                    or data.get("net_positions")
                    or []
                )
            else:
                positions = data or []

            if not isinstance(positions, list):
                positions = []

            securities = {}
            for p in positions:
                seg = self._normalize_segment(p.get("exchangeSegment") or p.get("exchange"))
                sid = p.get("securityId") or p.get("security_id")
                if seg and sid:
                    try:
                        securities.setdefault(seg, []).append(int(sid))
                    except Exception:
                        pass

            quotes = {}
            if securities:
                try:
                    qr = self._request(
                        "post",
                        f"{self.api_base}/marketfeed/ltp",
                        json=securities,
                        headers=self.headers,
                        timeout=self.timeout,
                    )
                    qdata = qr.json()
                    if isinstance(qdata, dict):
                        quotes = qdata.get("data", {})
                except Exception:
                    quotes = {}

            for p in positions:
                seg = self._normalize_segment(p.get("exchangeSegment") or p.get("exchange"))
                sid = str(p.get("securityId") or p.get("security_id"))
                quote = quotes.get(seg, {}).get(sid)
                ltp = None
                if isinstance(quote, dict):
                    ltp = quote.get("last_price") or quote.get("ltp")
                if ltp is None and p.get("lastTradedPrice") not in (None, ""):
                    ltp = p.get("lastTradedPrice")
                if ltp is not None:
                    try:
                        ltp_val = float(ltp)
                        p["last_price"] = ltp_val
                        p.setdefault("ltp", ltp_val)
                        buy_qty = float(p.get("buyQty") or p.get("buyqty") or 0)
                        sell_qty = float(p.get("sellQty") or p.get("sellqty") or 0)
                        net_qty = float(p.get("netQty") or p.get("netqty") or (buy_qty - sell_qty))
                        avg_buy = float(p.get("buyAvg") or p.get("buyavg") or 0)
                        avg_sell = float(p.get("sellAvg") or p.get("sellavg") or 0)
                        avg = avg_buy if net_qty > 0 else avg_sell
                        pnl = round((ltp_val - avg) * net_qty, 2)
                        p.setdefault("unrealizedProfit", pnl)
                        p.setdefault("profitAndLoss", pnl)
                    except Exception:
                        pass

            return {"status": "success", "data": positions}
        except requests.exceptions.Timeout:
            return {"status": "failure", "error": "Request to Dhan API timed out while fetching positions."}
        except requests.exceptions.RequestException as e:
            return {"status": "failure", "error": f"Failed to fetch positions from Dhan API: {str(e)}"}
        except json.JSONDecodeError:
            return {"status": "failure", "error": f"Invalid JSON response from Dhan API: {r.text}"}
        except Exception as e:
            return {"status": "failure", "error": f"An unexpected error occurred while fetching positions: {str(e)}"}

    def get_holdings(self):
        """Fetch portfolio holdings and enrich with LTP and P/L."""
        try:
            r = self._request(
                "get",
                f"{self.api_base}/holdings",
                headers=self.headers,
                timeout=self.timeout,
            )
            holdings = r.json()
            if isinstance(holdings, dict) and "data" in holdings:
                holdings = holdings.get("data")
            if not isinstance(holdings, list):
                holdings = []

            # Build securities dict for ticker API
            securities = {}
            for h in holdings:
                seg = self._normalize_segment(h.get("exchangeSegment") or h.get("exchange"))
                sid = h.get("securityId") or h.get("security_id")
                if seg and sid:
                    try:
                        securities.setdefault(seg, []).append(int(sid))
                    except Exception:
                        pass

            quotes = {}
            if securities:
                try:
                    qr = self._request(
                        "post",
                        f"{self.api_base}/marketfeed/ltp",
                        json=securities,
                        headers=self.headers,
                        timeout=self.timeout,
                    )
                    qdata = qr.json()
                    if isinstance(qdata, dict):
                        quotes = qdata.get("data", {})
                except Exception:
                    quotes = {}

            # Attach LTP and P/L if possible
            for h in holdings:
                seg = self._normalize_segment(h.get("exchangeSegment") or h.get("exchange"))
                sid = str(h.get("securityId") or h.get("security_id"))
                quote = quotes.get(seg, {}).get(sid)
                ltp = None
                if isinstance(quote, dict):
                    ltp = quote.get("last_price") or quote.get("ltp")

                if ltp is None and h.get("lastTradedPrice") not in (None, ""):
                    ltp = h.get("lastTradedPrice")

                if ltp is not None:
                    try:
                        ltp_val = float(ltp)
                        # expose LTP under both common keys
                        h["last_price"] = ltp_val
                        h.setdefault("ltp", ltp_val)
                        qty = float(h.get("availableQty") or h.get("totalQty") or 0)
                        avg = float(h.get("avgCostPrice") or 0)
                        pnl = round((ltp_val - avg) * qty, 2)
                        h["pnl"] = pnl
                        h.setdefault("unrealizedProfit", pnl)
                    except Exception:
                        pass

            return {"status": "success", "data": holdings}
        except requests.exceptions.Timeout:
            return {"status": "failure", "error": "Request to Dhan API timed out while fetching holdings."}
        except requests.exceptions.RequestException as e:
            return {"status": "failure", "error": "Failed to fetch holdings from Dhan API: {}".format(str(e))}
        except json.JSONDecodeError:
            return {"status": "failure", "error": "Invalid JSON response from Dhan API: {}".format(r.text)}
        except Exception as e:
            return {"status": "failure", "error": "An unexpected error occurred while fetching holdings: {}".format(str(e))}
            
    def get_profile(self):
        """
        Return profile or fund data to confirm account id.
        """
        try:
            r = self._request(
                "get",
               "{}/fundlimit".format(self.api_base),
                headers=self.headers,
                timeout=self.timeout, # Pass timeout
            )
            return {"status": "success", "data": r.json()}
        except requests.exceptions.Timeout:
            return {"status": "failure", "error": "Request to Dhan API timed out while fetching profile."}
        except requests.exceptions.RequestException as e:
            return {"status": "failure", "error": "Failed to fetch profile from Dhan API: {}".format(str(e))}
        except json.JSONDecodeError:
            return {"status": "failure", "error": "Invalid JSON response from Dhan API: {}".format(r.text)}
        except Exception as e:
            return {"status": "failure", "error": "An unexpected error occurred while fetching profile: {}".format(str(e))}

    def check_token_valid(self):
        """
        Validate access token and ensure it belongs to this client_id.
        """
        try:
            r = self._request(
                "get",
                "{}/fundlimit".format(self.api_base),
                headers=self.headers,
                timeout=5, # Explicit timeout for token check, can be shorter
            )
            r.raise_for_status()
            data = r.json()
            cid = str(data.get("clientId") or data.get("dhanClientId") or "").strip()
            if cid and cid != str(self.client_id):
                return False
            return True
        except requests.exceptions.Timeout:
            # Token validation timed out
            return False
        except requests.exceptions.RequestException:
            # Any other request error means token is likely invalid or API is down
            return False
        except json.JSONDecodeError:
            # Invalid JSON response from API during token check
            return False
        except Exception:
            # General unexpected error
            return False

    def get_opening_balance(self):
        """
        Fetch available cash balance from fundlimit API.
        """
        try:
            r = self._request(
                "get",
                "{}/fundlimit".format(self.api_base),
                headers=self.headers,
                timeout=self.timeout, # Pass timeout
            )
            data = r.json()
            for key in [
                "openingBalance",
                "netCashAvailable",
                "availableBalance",
                "availabelBalance", # Corrected typo from "availabelBalance" to "availableBalance" in the actual API call, but keeping here for robustness if API uses it.
                "withdrawableBalance",
                "availableAmount",
                "netCash",
            ]:
                if key in data:
                    return float(data[key])
            return float(data.get("cash", 0))
        except requests.exceptions.Timeout:
            return None # Or raise a specific error, depending on desired behavior
        except requests.exceptions.RequestException:
            return None
        except json.JSONDecodeError:
            return None
        except Exception:
            return None
